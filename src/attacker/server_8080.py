#!/usr/bin/env python3
import socketserver
import argparse
import threading
import datetime
import sys

class FullRequestHandler(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            # read headers
            headers = []
            while True:
                line = self.rfile.readline()
                if not line:
                    # client closed before headers complete
                    return
                line = line.decode('iso-8859-1').rstrip("\r\n")
                if line == "":
                    break
                headers.append(line)
            # parse start-line and headers
            start_line = headers[0] if headers else ""
            hdrs = {}
            for h in headers[1:]:
                parts = h.split(":", 1)
                if len(parts) == 2:
                    hdrs[parts[0].strip().lower()] = parts[1].strip()

            content_length = hdrs.get('content-length')
            body = b''
            complete = False

            if content_length:
                try:
                    to_read = int(content_length)
                except:
                    to_read = 0
                if to_read > 0:
                    body = self.rfile.read(to_read)
                    if len(body) == to_read:
                        complete = True
                else:
                    # zero-length body
                    complete = True
            else:
                # No Content-Length: read until client closes the connection (with timeout)
                # we'll attempt a non-blocking read loop until EOF
                self.request.settimeout(1.0)
                chunks = []
                try:
                    while True:
                        chunk = self.rfile.read(1024)
                        if not chunk:
                            break
                        chunks.append(chunk)
                except Exception:
                    # timeout or other -> treat as "connection closed" or end of data
                    pass
                body = b''.join(chunks)
                # If we got any bytes OR start-line present, consider it complete (heuristic)
                complete = True

            # Logging only if complete
            if complete:
                log_entry = {
                    'time': datetime.datetime.utcnow().isoformat() + "Z",
                    'client': f"{self.client_address[0]}:{self.client_address[1]}",
                    'start_line': start_line,
                    'headers': hdrs,
                    'body_len': len(body)
                }
                # write to logfile
                logfile = self.server.logfile
                with open(logfile, 'a') as f:
                    f.write(f"{log_entry['time']} {log_entry['client']} {log_entry['start_line']} headers={len(hdrs)} body_len={log_entry['body_len']}\n")
            # reply minimal HTTP 200 OK so clients get response
            try:
                self.wfile.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK")
            except:
                pass
        except Exception as e:
            # don't crash the server on odd clients
            pass

class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--logfile", default="/var/log/myservices/8080_requests.log")
    args = parser.parse_args()

    server = ThreadedTCPServer((args.bind, args.port), FullRequestHandler)
    server.logfile = args.logfile
    print(f"Listening on {args.bind}:{args.port}, logging complete requests to {args.logfile}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    server.server_close()

if __name__ == "__main__":
    main()
