# Scripts

This directory contains host-side helper scripts for validating, maintaining, and interacting with the lab.

These scripts are not Kubernetes workloads and are not deployed inside the target environments. They are meant to be executed from the repository root on the host machine.

## Available scripts

- `static_checks.py`: runs offline repository consistency checks.
- `open_tunnel.py`: opens a tunnel through the lab controller proxy.

---

# `static_checks.py`

## Purpose

`static_checks.py` validates the repository without starting the full lab.

It does not require Docker, Kind, Kubernetes, Helm, Skaffold, or Caldera to be running.

It is useful for catching configuration, syntax, and repository consistency issues before attempting a real deployment.

## What it checks

The script checks things such as:

- tracked generated/runtime artifacts in Git.
- target directory contract for templates, hooks, and Caldera assets.
- TOML parsing and basic validation for `configuration.conf`.
- Python syntax for repository Python files.
- Shell script syntax with `bash -n`.
- YAML parsing for non-template YAML files.
- Rendered compose template sanity with a sample environment.
- Skaffold local Helm chart dependency policy.
- Caldera ability/adversary consistency.
- Missing Caldera ability references.
- Duplicate or invalid Caldera IDs.
- Deprecated lab/controller references surfaced as warnings.

Generated files and heavy runtime artifacts are intentionally excluded, including:

- `__pycache__/`
- `.pytest_cache/`
- `*.pyc`
- generated build outputs
- runtime directories
- local cache directories
- vendored or generated artifacts that are not part of the lab/controller logic

## How to run

From the repository root:

```bash
python3 scripts/static_checks.py
python3 scripts/static_checks.py --json
python3 scripts/static_checks.py --check generated
```

---

# `open_tunnel.py`

## Purpose

`open_tunnel.py` opens a lab proxy tunnel by sending credentials to the lab controller proxy.
After the tunnel is accepted, regular HTTP requests to the proxy root are
forwarded to the selected upstream.

It performs an HTTP `POST` request to a tunnel endpoint exposed by the proxy. The request contains a JSON payload with a username and password.

The script is equivalent to running a `curl` command like this:

```bash
curl -fsS \
  -X POST \
  -H 'Content-Type: application/json' \
  --data '{"username":"...","password":"..."}' \
  '<proxy_url>/_tunnel/<name>/<port>'
```
