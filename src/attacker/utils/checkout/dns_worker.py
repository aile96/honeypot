#!/usr/bin/env python3
"""
Local HTTP worker that accepts POST /dig with a hostname in the body
and performs a 'dig +short <hostname>'. Results are logged to stdout (and optionally to a file).
"""

from http.server import BaseHTTPRequestHandler, HTTPServer
import os, subprocess, urllib.parse, logging, sys

BIND = os.environ.get("DNS_WORKER_BIND", "127.0.0.1")
PORT = int(os.environ.get("DNS_WORKER_PORT", "55354"))

# Configure logging (stdout for Kubernetes)
logger = logging.getLogger("dns_worker")
logger.setLevel(logging.INFO)
stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logger.addHandler(stream_handler)

# Optional file logging if LOG_PATH is set and writable
LOG_PATH = os.environ.get("LOG_PATH")
if LOG_PATH:
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        fh = logging.FileHandler(LOG_PATH)
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(fh)
        logger.info("Also logging to file: %s", LOG_PATH)
    except Exception as e:
        logger.warning("Cannot open log file %s: %s (stdout only)", LOG_PATH, e)

class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            length = int(self.headers.get('Content-Length', 0))
            raw = self.rfile.read(length) if length > 0 else b''
            body = raw.decode('utf-8', errors='ignore').strip()
            if self.headers.get('Content-Type', '').startswith('application/x-www-form-urlencoded'):
                body = urllib.parse.parse_qs(body).get('host', [body])[0]

            logger.info("Received dig request: %s", body)

            try:
                res = subprocess.run(["dig", "+short", body], capture_output=True, text=True, timeout=10)
                out = (res.stdout or "").strip()
                err = (res.stderr or "").strip()
                if out:
                    logger.info("dig +short %s -> %s", body, out.replace("\n", " | "))
                else:
                    logger.info("dig +short %s -> (no answer)", body)
                if err:
                    logger.warning("dig stderr: %s", err)
            except Exception as e:
                logger.exception("dig command failed: %s", e)
                out = ""

            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write((out + "\n").encode("utf-8"))
        except Exception as e:
            logger.exception("Handler failure: %s", e)
            self.send_response(500)
            self.end_headers()

    def log_message(self, format, *args):
        logger.debug("%s - - %s" % (self.client_address[0], format % args))

if __name__ == "__main__":
    server = HTTPServer((BIND, PORT), Handler)
    logger.info("Starting dns_worker on %s:%d", BIND, PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    logger.info("Shutting down dns_worker")
    server.server_close()
