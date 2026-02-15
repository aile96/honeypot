from datetime import datetime, timedelta, timezone
import atexit
import logging
import os
from typing import Optional

import jwt
from fastapi import FastAPI, HTTPException
from opentelemetry import metrics, trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from pydantic import BaseModel, Field
from sqlalchemy import Column, Integer, String, create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import declarative_base, Session
from sqlalchemy.engine import URL

# ---- Config ----
def get_database_url():
    # If already exixts DATABASE_URL, use it as it is
    if os.getenv("DATABASE_URL"):
        return os.getenv("DATABASE_URL")

    # Otherwise building the URL
    return URL.create(
        drivername=os.getenv("DB_DRIVER", "postgresql+psycopg"),
        username=os.getenv("DB_USER", "testuser"),
        password=os.getenv("DB_PASS", "testpass"),
        host=os.getenv("DB_HOST", "postgres"),
        port=int(os.getenv("DB_PORT", "5432")),
        database=os.getenv("DB_NAME", "usersdb"),
        # optional: query param, es. sslmode=require
        # query=dict(item.split("=", 1) for item in os.getenv("DB_QUERY", "").split("&") if item)
    )
DATABASE_URL = get_database_url()

JWT_SECRET = os.getenv("JWT_SECRET", "CHANGEME_SUPER_SECRET")
JWT_ALG = os.getenv("JWT_ALG", "HS256")
JWT_EXP_MINUTES = int(os.getenv("JWT_EXP_MINUTES", "60"))

# ---- DB setup ----
engine = create_engine(DATABASE_URL, future=True, echo=False)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(255), unique=True, nullable=False, index=True)
    password = Column(String(255), nullable=False)
    email = Column(String(64))
    address = Column(String(64))
    zip = Column(String(64))
    city = Column(String(64))
    state = Column(String(64))
    country = Column(String(64))
    phone = Column(String(64))

Base.metadata.create_all(engine)

# ---- API ----
app = FastAPI(title="Auth Service", version="1.0.0")


def configure_telemetry():
    service_name = os.getenv("OTEL_SERVICE_NAME", "auth")
    resource = Resource.create({"service.name": service_name})

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(insecure=True)))
    trace.set_tracer_provider(tracer_provider)

    metric_reader = PeriodicExportingMetricReader(OTLPMetricExporter(insecure=True))
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    metrics.set_meter_provider(meter_provider)

    logger_provider = LoggerProvider(resource=resource)
    set_logger_provider(logger_provider)
    logger_provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter(insecure=True)))
    logging.getLogger().addHandler(LoggingHandler(level=logging.NOTSET, logger_provider=logger_provider))

    LoggingInstrumentor().instrument(set_logging_format=True)
    SQLAlchemyInstrumentor().instrument(engine=engine)
    FastAPIInstrumentor.instrument_app(app)

    @atexit.register
    def shutdown_telemetry():
        try:
            logger_provider.shutdown()
        except Exception:
            pass
        try:
            meter_provider.shutdown()
        except Exception:
            pass
        try:
            tracer_provider.shutdown()
        except Exception:
            pass


configure_telemetry()

class RegisterReq(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)
    email: Optional[str] = None
    address: Optional[str] = None
    zip: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    phone: Optional[str] = None

class LoginReq(BaseModel):
    username: str
    password: str

class VerifyReq(BaseModel):
    token: str

@app.get("/health")
def health():
    # simple liveness probe + quick DB check
    try:
        with Session(engine) as s:
            s.execute(select(User.id)).first()
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/register", status_code=201)
def register(req: RegisterReq):
    with Session(engine) as s:
        exists = s.scalar(select(User).where(User.username == req.username))
        if exists:
            raise HTTPException(status_code=409, detail="Username already exists")
        user = User(
            username=req.username,
            password=req.password,
            email=req.email,
            address=req.address,
            zip=req.zip,
            city=req.city,
            state=req.state,
            country=req.country,
            phone=req.phone,
        )
        s.add(user)
        try:
            s.commit()
        except IntegrityError as exc:
            s.rollback()
            err_msg = str(exc.orig) if getattr(exc, "orig", None) else str(exc)
            if "idx_users_email" in err_msg or "(email)" in err_msg:
                raise HTTPException(status_code=409, detail="Email already exists") from exc
            if "idx_users_username" in err_msg or "(username)" in err_msg:
                raise HTTPException(status_code=409, detail="Username already exists") from exc
            raise HTTPException(status_code=409, detail="User already exists") from exc
        return {"status": "created", "id": user.id}

@app.post("/login")
def login(req: LoginReq):
    with Session(engine) as s:
        user = s.scalar(select(User).where(User.username == req.username))
        if not user or user.password != req.password:
            raise HTTPException(status_code=401, detail="Invalid credentials")

    now = datetime.now(timezone.utc)
    payload = {
        "sub": req.username,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=JWT_EXP_MINUTES)).timestamp()),
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)
    return {"token": token}

@app.post("/verify")
def verify(req: VerifyReq):
    try:
        payload = jwt.decode(req.token, JWT_SECRET, algorithms=[JWT_ALG])
        return {"valid": True, "payload": payload}
    except jwt.ExpiredSignatureError:
        return {"valid": False, "error": "expired"}
    except jwt.InvalidTokenError as e:
        return {"valid": False, "error": str(e)}
