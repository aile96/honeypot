# Kubernetes Honeypot Lab

Local Kubernetes honeypot and adversary-emulation lab for research, training, and defensive validation. The lab runs on the local Docker/Kind environment and can deploy either the OpenTelemetry target or the 5Gcore target with MITRE Caldera kill chains.

Do not point this project at production clusters or third-party infrastructure.

## Entry Point

Run commands from the repository root:

```bash
./start.sh
```

`start.sh` loads `configuration.conf`, then preserves explicit environment overrides such as:

```bash
LAB_NAME=demo-otel CLUSTER_TARGET=opentelemetry ./start.sh
LAB_NAME=demo-5g CLUSTER_TARGET=5Gcore ./start.sh
```

Each lab is scoped by `LAB_NAME`. For example `LAB_NAME=demo-otel` creates a controller named `demo-otel-controller`, a Compose project named `honeypot-demo-otel`, a Kind cluster named `demo-otel`, a Docker network named `kind-demo-otel`, runtime state under `res/runtime/demo-otel`, and results under `res/results/demo-otel`.

## Targets

Select the target with:

```text
CLUSTER_TARGET=opentelemetry
CLUSTER_TARGET=5Gcore
```

Target defaults live in:

```text
src/<CLUSTER_TARGET>/conf-files/variables.py
```

The controller mounts `src/<CLUSTER_TARGET>` and runs the target pipeline hooks, Caldera assets, controller hooks, and restore hooks from that tree.

## Docker Modes

`HOST_SOCKET=false` runs an isolated Docker daemon inside the controller. This supports multiple labs at the same time because each controller owns its own nested Docker resources.

`HOST_SOCKET=true` mounts the host Docker socket. Only one host-socket lab may run at a time; a second host-socket start fails before creating resources.

Host exposure is controlled by:

```text
EXPOSE_TO_HOST=true
PROXY_BIND_ALL=false
```

When exposure is enabled, the controller proxy publishes container port `18080` to a dynamic host port. The selected port and bind address are written to:

```text
res/runtime/<LAB_NAME>/info
```

`PROXY_BIND_ALL=false` binds the proxy to `127.0.0.1`. `PROXY_BIND_ALL=true` binds it to `0.0.0.0`. `EXPOSE_TO_HOST=false` publishes no host proxy port.

## Runtime Flow

The controller image is built from:

```text
src/lab/lab-controller/Dockerfile
```

The startup flow is:

```text
start.sh
  -> configuration.conf plus environment overrides
  -> src/lab/lab-controller/entrypoint.py
  -> /app/start_lab.py
  -> /app/pipeline/*.py
  -> /start_controller.py
```

The pipeline renders target templates, creates or reuses the lab-specific Kind cluster, builds Compose and Skaffold artifacts, starts target Compose services, deploys the target Helm/Skaffold stack, and runs target-specific pipeline hooks.

After the pipeline completes, `/start_controller.py` connects to Caldera, discovers adversaries in `src/<CLUSTER_TARGET>/caldera/adversaries`, waits for agents, runs kill chains in filename order, calls target controller hooks, and writes the kill-chain summary.

## Local Endpoints

Read the active proxy endpoint from:

```bash
python3 -m json.tool res/runtime/<LAB_NAME>/info
```

Common proxy routes include:

- `/healthz` for controller proxy health
- `/caldera/` for Caldera when enabled
- `/frontend/` for the OpenTelemetry frontend when the target exposes it
- `/registry/` for the local registry route when enabled

The pipeline also writes a host-ready kubeconfig for each lab:

```bash
export KUBECONFIG="$PWD/res/runtime/<LAB_NAME>/kubeconfig"
kubectl get pods -A
```

For `HOST_SOCKET=true`, that kubeconfig points directly to the Kind API port published by the host Docker daemon. For `HOST_SOCKET=false`, it points to the controller proxy port and the proxy tunnels Kubernetes API TLS traffic to the nested Kind cluster. In both modes the file keeps `kind-<LAB_NAME>` as the current context, so `--context` is optional when `KUBECONFIG` points to this file.

Caldera credentials are local lab credentials from the mounted Caldera config:

- `admin / admin`
- `red / admin`
- `blue / admin`

## Results

Results are preserved under:

```text
res/results/<LAB_NAME>
```

Important files and directories include:

- `killchain-summary.json`
- `KC*`
- `caldera`
- `kube_events`
- `telemetry`
- `frontend-port-forward.log` for OpenTelemetry

Runtime state is disposable and kept under:

```text
res/runtime/<LAB_NAME>
```

Build caches are kept under:

```text
res/cache
```

## Quick Verification

Set the lab name you started:

```bash
LAB_NAME=honeypotlab
```

Then inspect the runtime state and local resources:

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
curl "http://127.0.0.1:${PORT}/caldera/"
```

## Configuration Notes

Most user-facing options live in:

```text
configuration.conf
```

Common options:

- `LAB_NAME=honeypotlab`
- `CLUSTER_TARGET=opentelemetry` or `CLUSTER_TARGET=5Gcore`
- `BUILD_CONTROLLER=true`
- `FOLLOW_CONTROLLER_LOGS=false`
- `HOST_SOCKET=false`
- `EXPOSE_TO_HOST=true`
- `PROXY_BIND_ALL=false`
- `SKIP_RESOURCE_CHECK=false`

`RESTORE_LAB` and `RESTORE_LAB_MODE` are target defaults in `src/<target>/conf-files/variables.py`; they are not declared in `configuration.conf`.

Legacy `CLUSTER_PROFILE` and `GENERIC_SVC_PORT` are not required for new runs.

## Cleanup

Clean a specific lab with:

```bash
./remove_all.sh <LAB_NAME>
```

If no argument is supplied, `remove_all.sh` uses the configured `LAB_NAME`.

Cleanup is scoped to the selected lab. It removes the matching controller container, lab Compose project, lab Kind cluster, lab Docker network, and `res/runtime/<LAB_NAME>`. It preserves `res/results/<LAB_NAME>` and build caches.

Example:

```bash
./remove_all.sh honeypotlab
```

Verify cleanup:

```bash
LAB_NAME=honeypotlab
docker ps -a --format '{{.Names}} {{.Labels}}' | grep "${LAB_NAME}" || true
docker network ls --format '{{.Name}} {{.Labels}}' | grep "${LAB_NAME}" || true
kind get clusters | grep "${LAB_NAME}" || true
test ! -d "res/runtime/${LAB_NAME}"
```

## Repository Map

```text
start.sh
remove_all.sh
configuration.conf
src/lab/lab-controller/
src/lab/lab-controller/app/start_lab.py
src/lab/lab-controller/app/pipeline/
src/lab/lab-controller/start_controller.py
src/opentelemetry/
src/5Gcore/
src/common/
res/runtime/
res/results/
```
