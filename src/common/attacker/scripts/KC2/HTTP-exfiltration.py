#!/usr/bin/env python3

import os
import tarfile
import argparse
import http.client
from pathlib import Path
from urllib.parse import urlparse


def create_archive(data_path: str) -> Path:
    base_dir = Path(data_path) / "KC2"
    output_tgz = base_dir / "analysis" / "kc2-child-results.tgz"

    output_tgz.parent.mkdir(parents=True, exist_ok=True)

    def exclude_output_file(tarinfo):
        if tarinfo.name in {
            "./analysis/kc2-child-results.tgz",
            "analysis/kc2-child-results.tgz",
        }:
            return None
        return tarinfo

    with tarfile.open(output_tgz, "w:gz") as tar:
        tar.add(base_dir, arcname=".", filter=exclude_output_file)

    return output_tgz


def send_archive(archive_path: Path, server_url: str) -> None:
    parsed = urlparse(server_url)

    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Server URL must start with http:// or https://")

    host = parsed.hostname
    port = parsed.port

    if not host:
        raise ValueError("Invalid server host")

    if port is None:
        port = 443 if parsed.scheme == "https" else 80

    path = parsed.path or "/upload"

    conn_class = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection

    file_size = archive_path.stat().st_size

    headers = {
        "Content-Type": "application/gzip",
        "Content-Length": str(file_size),
        "X-Filename": archive_path.name,
    }

    with open(archive_path, "rb") as f:
        conn = conn_class(host, port)
        conn.request("POST", path, body=f, headers=headers)

        response = conn.getresponse()
        response_body = response.read().decode(errors="replace")

        print(f"Server response: {response.status} {response.reason}")
        print(response_body)

        conn.close()

        if response.status < 200 or response.status >= 300:
            raise RuntimeError(f"Upload failed with status {response.status}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-path",
        default=os.environ.get("DATA_PATH"),
        help="Base DATA_PATH. Defaults to the DATA_PATH environment variable.",
    )
    parser.add_argument(
        "--server-url",
        required=True,
        help="Destination server URL, for example http://192.168.1.50:8080/upload",
    )

    args = parser.parse_args()

    if not args.data_path:
        raise ValueError("DATA_PATH is not set. Use --data-path or set the DATA_PATH environment variable.")

    print("Creating archive...")
    archive_path = create_archive(args.data_path)
    print(f"Archive created: {archive_path}")

    print("Sending archive...")
    send_archive(archive_path, args.server_url)
    print("Upload completed successfully.")


if __name__ == "__main__":
    main()