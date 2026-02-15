import atexit
import logging
import os
import signal
import socket
import threading
import time
from urllib.parse import urlparse

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


STOP_EVENT = threading.Event()


def configure_telemetry():
    service_name = os.getenv("OTEL_SERVICE_NAME", "smtp")
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

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    if not any(isinstance(handler, logging.StreamHandler) for handler in root_logger.handlers):
        stream_handler = logging.StreamHandler()
        stream_handler.setLevel(logging.INFO)
        stream_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        root_logger.addHandler(stream_handler)
    if not any(isinstance(handler, LoggingHandler) for handler in root_logger.handlers):
        root_logger.addHandler(LoggingHandler(level=logging.NOTSET, logger_provider=logger_provider))

    logger = logging.getLogger("smtp")
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

    return logger


LOGGER = configure_telemetry()
TRACER = trace.get_tracer("smtp")
METER = metrics.get_meter("smtp")

CONNECTIONS_COUNTER = METER.create_counter("app.smtp.connections")
ERRORS_COUNTER = METER.create_counter("app.smtp.errors")
CONNECTION_DURATION_SECONDS = METER.create_histogram("app.smtp.connection.duration", unit="s")
COMMAND_SIZE_BYTES = METER.create_histogram("app.smtp.command.size", unit="By")


def command_name(packet):
    if not packet:
        return "EMPTY"
    command = packet.split(maxsplit=1)[0].upper()
    known_commands = {"HELO", "EHLO", "MAIL", "RCPT", "DATA", "RSET", "NOOP", "VRFY", "EXPN", "QUIT", "HELP"}
    return command if command in known_commands else "OTHER"


def parse_smtp_port(value, default=25):
    if value is None:
        return default

    raw = str(value).strip()
    if raw == "":
        return default

    try:
        return int(raw)
    except ValueError:
        pass

    # Kubernetes service-link env vars can look like: tcp://10.101.53.117:25
    parsed = urlparse(raw)
    if parsed.port is not None:
        return parsed.port

    # Fallback for simple host:port values.
    host, sep, port_part = raw.rpartition(":")
    if sep and host and port_part.isdigit():
        return int(port_part)

    raise ValueError(f"Invalid SMTP port value: {value!r}")


def handle_signal(signum, _frame):
    LOGGER.info("Received signal %s, shutting down SMTP server", signum)
    STOP_EVENT.set()


def server(host="0.0.0.0", port=25):
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind((host, port))
    server_socket.listen(5)
    server_socket.settimeout(1.0)
    LOGGER.info("SMTP server listening on %s:%s", host, port)

    with TRACER.start_as_current_span(
        "smtp.server",
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
                    "smtp.connection",
                    kind=SpanKind.SERVER,
                    attributes={
                        "network.peer.address": peer,
                        "network.peer.port": peer_port,
                    },
                ) as connection_span:
                    try:
                        conn.sendall(b"220 Service Ready\r\n")
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
                                conn.sendall(b"221 Bye\r\n")
                                break

                            conn.sendall(b"250 OK\r\n")
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
            LOGGER.info("SMTP server stopped")


if __name__ == "__main__":
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    smtp_host = os.getenv("SMTP_HOST", "0.0.0.0")
    smtp_port = parse_smtp_port(
        os.getenv("SMTP_LISTEN_PORT") or os.getenv("SMTP_PORT"),
        default=25,
    )
    server(host=smtp_host, port=smtp_port)
