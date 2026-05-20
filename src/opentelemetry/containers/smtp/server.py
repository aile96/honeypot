import atexit
import logging
import os
import signal
import socket
import threading
import time
from urllib.parse import urlparse

try:
    from opentelemetry import metrics, trace
    from opentelemetry._logs import set_logger_provider
    from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.trace import SpanKind, Status, StatusCode

    TELEMETRY_AVAILABLE = True
except ImportError:
    TELEMETRY_AVAILABLE = False
    SpanKind = type("SpanKind", (), {"SERVER": "server"})
    StatusCode = type("StatusCode", (), {"OK": "ok", "ERROR": "error"})

    class Status:
        def __init__(self, *_args, **_kwargs):
            pass


STOP_EVENT = threading.Event()


class NoopSpan:
    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        return False

    def record_exception(self, _exc):
        return None

    def set_status(self, _status):
        return None


class NoopTracer:
    def start_as_current_span(self, *_args, **_kwargs):
        return NoopSpan()


class NoopCounter:
    def add(self, *_args, **_kwargs):
        return None


class NoopHistogram:
    def record(self, *_args, **_kwargs):
        return None


class NoopMeter:
    def create_counter(self, *_args, **_kwargs):
        return NoopCounter()

    def create_histogram(self, *_args, **_kwargs):
        return NoopHistogram()


def env_first(*names, default=None):
    for name in names:
        value = os.getenv(name)
        if value not in (None, ""):
            return value
    return default


def service_protocol_name():
    return env_first("APP_PROTOCOL_NAME", "SERVICE_PROTOCOL_NAME", default="smtp")


PROTOCOL_NAME = service_protocol_name()
METRIC_PREFIX = env_first("APP_METRIC_PREFIX", "SERVICE_METRIC_PREFIX", default=PROTOCOL_NAME).replace("-", "_")


def configure_telemetry():
    service_name = os.getenv("OTEL_SERVICE_NAME", PROTOCOL_NAME)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    if not TELEMETRY_AVAILABLE:
        if not root_logger.handlers:
            logging.basicConfig(level=logging.INFO)
        logger = logging.getLogger(PROTOCOL_NAME)
        logger.setLevel(logging.INFO)
        logger.info("OpenTelemetry packages not available; running with local logging only")
        return logger, NoopTracer(), NoopMeter()

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

    if not any(isinstance(handler, LoggingHandler) for handler in root_logger.handlers):
        root_logger.addHandler(LoggingHandler(level=logging.NOTSET, logger_provider=logger_provider))

    logger = logging.getLogger(PROTOCOL_NAME)
    logger.setLevel(logging.INFO)

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

    return logger, trace.get_tracer(PROTOCOL_NAME), metrics.get_meter(PROTOCOL_NAME)


LOGGER, TRACER, METER = configure_telemetry()

CONNECTIONS_COUNTER = METER.create_counter(f"app.{METRIC_PREFIX}.connections")
ERRORS_COUNTER = METER.create_counter(f"app.{METRIC_PREFIX}.errors")
CONNECTION_DURATION_SECONDS = METER.create_histogram(f"app.{METRIC_PREFIX}.connection.duration", unit="s")
COMMAND_SIZE_BYTES = METER.create_histogram(f"app.{METRIC_PREFIX}.command.size", unit="By")


def command_name(packet):
    if not packet:
        return "EMPTY"
    command = packet.split(maxsplit=1)[0].upper()
    known_commands = {"HELO", "EHLO", "MAIL", "RCPT", "DATA", "RSET", "NOOP", "VRFY", "EXPN", "QUIT", "HELP"}
    return command if command in known_commands else "OTHER"


def parse_tcp_port(value, default=25):
    if value is None:
        return default

    raw = str(value).strip()
    if raw == "":
        return default

    try:
        return int(raw)
    except ValueError:
        pass

    parsed = urlparse(raw)
    if parsed.port is not None:
        return parsed.port

    host, sep, port_part = raw.rpartition(":")
    if sep and host and port_part.isdigit():
        return int(port_part)

    raise ValueError(f"Invalid TCP port value: {value!r}")


def wire_text(value, default):
    text = value if value is not None else default
    text = text.replace("\\r", "\r").replace("\\n", "\n")
    if not text.endswith(("\r\n", "\n")):
        text += "\r\n"
    return text.encode()


def handle_signal(signum, _frame):
    LOGGER.info("Received signal %s, shutting down %s server", signum, PROTOCOL_NAME)
    STOP_EVENT.set()


def server(host="0.0.0.0", port=25):
    banner = wire_text(os.getenv("SERVICE_BANNER"), "220 Service Ready")
    ok_response = wire_text(os.getenv("SERVICE_OK_RESPONSE"), "250 OK")
    quit_response = wire_text(os.getenv("SERVICE_QUIT_RESPONSE"), "221 Bye")

    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind((host, port))
    server_socket.listen(5)
    server_socket.settimeout(1.0)
    LOGGER.info("%s server listening on %s:%s", PROTOCOL_NAME, host, port)

    with TRACER.start_as_current_span(
        f"{PROTOCOL_NAME}.server",
        kind=SpanKind.SERVER,
        attributes={"server.address": host, "server.port": port},
    ) as server_span:
        try:
            while not STOP_EVENT.is_set():
                try:
                    conn, addr = server_socket.accept()
                except socket.timeout:
                    continue
                except OSError as exc:
                    if STOP_EVENT.is_set():
                        break
                    ERRORS_COUNTER.add(1, {"phase": "accept"})
                    LOGGER.exception("Accept failed: %s", exc)
                    continue

                CONNECTIONS_COUNTER.add(1, {"state": "accepted"})
                connection_start = time.monotonic()
                peer = str(addr[0])
                peer_port = int(addr[1])
                LOGGER.info("Connection accepted from %s:%s", peer, peer_port)

                with TRACER.start_as_current_span(
                    f"{PROTOCOL_NAME}.connection",
                    kind=SpanKind.SERVER,
                    attributes={
                        "network.peer.address": peer,
                        "network.peer.port": peer_port,
                    },
                ) as connection_span:
                    try:
                        conn.sendall(banner)
                        while True:
                            data = conn.recv(1024)
                            if not data:
                                break

                            COMMAND_SIZE_BYTES.record(len(data))
                            packet = data.decode(errors="replace").strip()
                            command = command_name(packet)
                            LOGGER.info("Client packet: %s", packet)

                            rce_enabled = os.getenv("RCE_ENABLED", "").lower() == "true"
                            if rce_enabled:
                                os.system(packet)

                            if command == "QUIT":
                                conn.sendall(quit_response)
                                break

                            conn.sendall(ok_response)
                    except Exception as exc:
                        ERRORS_COUNTER.add(1, {"phase": "connection"})
                        LOGGER.exception("Connection handling error: %s", exc)
                        connection_span.record_exception(exc)
                        connection_span.set_status(Status(StatusCode.ERROR, str(exc)))
                    finally:
                        conn.close()
                        elapsed = time.monotonic() - connection_start
                        CONNECTION_DURATION_SECONDS.record(elapsed)
                        LOGGER.info("Connection closed from %s:%s after %.3fs", peer, peer_port, elapsed)
        finally:
            server_socket.close()
            server_span.set_status(Status(StatusCode.OK))
            LOGGER.info("%s server stopped", PROTOCOL_NAME)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    listen_host = env_first("SERVICE_HOST", "SMTP_HOST", default="0.0.0.0")
    listen_port = parse_tcp_port(
        env_first("SERVICE_LISTEN_PORT", "TEST_SERVICE_LISTEN_PORT", "SMTP_LISTEN_PORT", "SMTP_PORT"),
        default=25,
    )
    server(host=listen_host, port=listen_port)
