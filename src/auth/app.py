from datetime import datetime, timedelta, timezone
import os
from typing import Optional

import jwt
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import Column, Integer, String, create_engine, select
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
        s.commit()
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