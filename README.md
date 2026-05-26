# Kubernetes Honeypot Lab

Local Kubernetes honeypot and adversary-emulation lab for research, training, and defensive validation. The lab runs with Docker and Kind and can deploy either the default **5Gcore** target or the OpenTelemetry demo target with MITRE Caldera kill chains.

Do not point this project at production clusters or third-party infrastructure.

## Prerequisites

Runtime use expects these tools on the host:

- Python 3.11+
- Docker
- Kind
- kubectl
- Helm
- Skaffold
- Bash

For the default 5Gcore target, keep enough local resources available for a multi-node Kind cluster and supporting containers. The default `configuration.conf` currently asks for 16 GB available RAM and 6 CPU cores unless `SKIP_RESOURCE_CHECK=true` is set.

## Configuration

The root `configuration.conf` is the source of truth. Environment variable overrides are intentionally not supported by `start.py`.

To change the lab name, target, Docker mode, proxy exposure, restore settings, or target-specific defaults, edit `configuration.conf` or pass a different config file:

```bash
./start.py --config path/to/configuration.conf
```

The default target is:

```toml
[lab]
CLUSTER_TARGET = "5Gcore"
LAB_NAME = "honeypotlab"
```

Supported target values are:

```text
5Gcore
opentelemetry
```

The controller mounts `src/<CLUSTER_TARGET>` and uses the selected target's templates, hooks, Caldera assets, controller hooks, and restore hooks.

## Start the Lab

Run commands from the repository root:

```bash
./start.py
```

Each lab is scoped by `LAB_NAME`. With the default `LAB_NAME=honeypotlab`, the project creates or uses resources such as:

```text
controller container: honeypotlab-controller
Compose project:      honeypot-honeypotlab
Kind cluster/context: kind-honeypotlab
runtime state:        res/runtime/honeypotlab
results:              res/results/honeypotlab
```

## Docker Modes

`HOST_SOCKET=false` runs an isolated Docker daemon inside the controller. This is the safer mode for multiple independent labs because each controller owns its nested Docker resources.

`HOST_SOCKET=true` mounts the host Docker socket. Only one host-socket lab should be active at a time; `start.py` checks the runtime metadata for other active host-socket labs before starting.

Host exposure is controlled by:

```toml
EXPOSE_TO_HOST = true
PROXY_BIND_ALL = false
```

When exposure is enabled in internal-Docker mode, the controller proxy publishes container port `18080` to a dynamic host port. The selected bind address and port are written to:

```text
res/runtime/<LAB_NAME>/info
```

`PROXY_BIND_ALL=false` binds the proxy to `127.0.0.1`. `PROXY_BIND_ALL=true` binds it to `0.0.0.0`. `EXPOSE_TO_HOST=false` publishes no host proxy port.

## Runtime Flow

The controller image is built from:

```text
src/lab/lab-controller/Dockerfile
```

Startup flow:

```text
start.py
  -> load configuration.conf
  -> derive runtime paths and controller settings
  -> create/check Docker network and registry cache
  -> write res/runtime/<LAB_NAME>/config.toml
  -> start src/lab/lab-controller/entrypoint.py
  -> run /app/start_lab.py
  -> run /app/pipeline/*.py
  -> run /app/start_caldera.py
```

The pipeline renders target templates, creates or reuses the lab-specific Kind cluster, prepares Compose and Skaffold artifacts, starts target Compose services, deploys the target Helm/Skaffold stack, and runs target-specific pipeline hooks.

After the pipeline is ready, `/app/start_caldera.py` connects to Caldera, discovers adversaries in `src/<CLUSTER_TARGET>/caldera/adversaries`, waits for agents, runs kill chains in filename order, calls target controller hooks, optionally restores lab state, and writes the kill-chain summary.

Set `AUTOREMOVE_LAB=true` in `[lab]` to run the controller as a one-shot lab: after `/app/start_caldera.py` finishes the kill-chain sequence, the controller shuts down, performs its best-effort lab cleanup, removes `res/runtime/<LAB_NAME>`, and Docker removes the controller container. The default is `false`, so labs remain available after the kill chains unless cleanup is requested.

## Local Endpoints

Read the active proxy endpoint from:

```bash
python3 -m json.tool res/runtime/<LAB_NAME>/info
```

The controller proxy always exposes:

- `/healthz` for controller proxy health

Default HTTP routes to Caldera, the registry, and target frontends are intentionally not exposed. Runtime code can register explicit authenticated HTTP tunnels through the proxy when needed. In internal-Docker mode the same proxy also tunnels Kubernetes API TLS traffic for the generated host kubeconfig.

The pipeline also writes a host-ready kubeconfig:

```bash
export KUBECONFIG="$PWD/res/runtime/<LAB_NAME>/kubeconfig"
kubectl get pods -A
```

For `HOST_SOCKET=true`, that kubeconfig points directly to the Kind API port published by the host Docker daemon. For `HOST_SOCKET=false`, it points to the controller proxy port and the proxy tunnels Kubernetes API TLS traffic to the nested Kind cluster.

Caldera credentials are local lab credentials from the mounted Caldera config:

```text
admin / admin
red   / admin
blue  / admin
```

## Results and Runtime Files

Results are preserved under:

```text
res/results/<LAB_NAME>
```

Important outputs include:

- `killchain-summary.json`
- `KC*` directories/files
- `caldera`
- `kube_events`
- `telemetry`
- `frontend-port-forward.log` for OpenTelemetry

Runtime state is disposable and kept under:

```text
res/runtime/<LAB_NAME>
```

The only persistent build/image cache is the local registry storage:

```text
res/cache/docker
```

## Quick Verification

Set the lab name from `configuration.conf`:

```bash
LAB_NAME=honeypotlab
```

Inspect runtime metadata and local resources:

```bash
python3 -m json.tool "res/runtime/${LAB_NAME}/info"
docker ps -a --filter "name=${LAB_NAME}-controller"
docker port "${LAB_NAME}-controller" 18080/tcp
```

For `HOST_SOCKET=false`, inspect the nested Docker daemon from the controller:

```bash
docker exec "${LAB_NAME}-controller" docker ps
docker exec "${LAB_NAME}-controller" kind get clusters
docker exec "${LAB_NAME}-controller" kubectl --context "kind-${LAB_NAME}" get pods -A
```

For `HOST_SOCKET=true`, the controller uses the host Docker daemon:

```bash
docker exec "${LAB_NAME}-controller" docker ps
kind get clusters
```

From the host, use the generated kubeconfig in either Docker mode:

```bash
export KUBECONFIG="$PWD/res/runtime/${LAB_NAME}/kubeconfig"
kubectl get nodes
kubectl get pods -A
```

After the controller proxy is exposed:

```bash
PORT=$(python3 - <<'PY'
import json, os
info=json.load(open(f"res/runtime/{os.environ['LAB_NAME']}/info"))
print(info["controller_proxy"]["host_port"])
PY
)
curl "http://127.0.0.1:${PORT}/healthz"
```

## Cleanup

Clean a specific lab with:

```bash
./remove_all.py <LAB_NAME>
```

If no argument is supplied, `remove_all.py` uses `LAB_NAME` from `configuration.conf`.

Cleanup is scoped to the selected lab. The host cleanup script signals the controller, waits for it to stop, removes the matching controller container, removes `res/runtime/<LAB_NAME>`, and removes the shared registry cache only when no active labs use it. Compose stack, Kind cluster, and lab network cleanup are handled best-effort by the controller entrypoint during shutdown. Results under `res/results/<LAB_NAME>` are preserved.

For automatic cleanup at the end of a kill-chain run, set `AUTOREMOVE_LAB=true` before starting the lab. Results under `res/results/<LAB_NAME>` are still preserved.

Example:

```bash
./remove_all.py honeypotlab
```

Verify cleanup:

```bash
LAB_NAME=honeypotlab
docker ps -a --format '{{.Names}} {{.Labels}}' | grep "${LAB_NAME}" || true
docker network ls --format '{{.Name}} {{.Labels}}' | grep "${LAB_NAME}" || true
kind get clusters | grep "${LAB_NAME}" || true
test ! -d "res/runtime/${LAB_NAME}"
```

## Development Test Setup

The test suite added for this repository focuses only on lab/controller/orchestration code. It intentionally does not test the application services under target `containers/` directories.

Install development dependencies:

```bash
python3 -m pip install -r requirements-dev.txt
```

Run all offline tests:

```bash
python3 -m pytest
```

Run only unit tests:

```bash
python3 -m pytest -m unit
```

Run static repository integrity checks:

```bash
python3 -m pytest -m static
python3 scripts/static_checks.py
```

These tests are intended for local/manual use. They are not wired into a CI pipeline.

The current test coverage includes:

- root TOML parsing and target merge behavior
- explicit confirmation that environment overrides do not replace `configuration.conf`
- host bootstrap config derivation and runtime metadata writing
- template rendering helpers
- controller config parsing, bool/int validation, and env conversion
- pipeline script discovery, hook lookup, and retry policy resolution
- pipeline state lifecycle and resume metadata
- Kind template helper generation
- controller proxy health, dynamic-route, and Kubernetes API tunnel helpers
- Caldera kill-chain controller helper logic
- static compile/config/Caldera integrity checks for orchestration files

## Repository Map

```text
start.py                                      host bootstrap entry point
remove_all.py                                 scoped host cleanup entry point
configuration.conf                            default lab and target configuration
requirements-dev.txt                          local pytest/static-check dependencies
pyproject.toml                                pytest configuration
scripts/static_checks.py                      manual repository integrity checks
src/lab/lib/                                  host-side bootstrap helpers
src/lab/lab-controller/                       controller image entrypoints and libraries
src/lab/lab-controller/app/start_lab.py       pipeline runner
src/lab/lab-controller/app/pipeline/          built-in orchestration pipeline steps
src/5Gcore/                                   default target assets, templates, hooks, Caldera data
src/opentelemetry/                            optional target assets, templates, hooks, Caldera data
src/common/                                   shared controller/proxy/attacker/caldera container assets
tests/                                        offline pytest suite for lab/controller/orchestration
res/runtime/                                  generated runtime state, disposable
res/results/                                  generated results, preserved by cleanup
res/cache/docker                              local registry image cache
```
