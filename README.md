# Kubernetes Honeypot Lab

Local Kubernetes honeypot and adversary-emulation lab for research, training, and defensive validation. The default flow runs entirely on the local Docker/KinD environment and deploys an adapted OpenTelemetry Astronomy Shop, telemetry components, MITRE Caldera, and simulated kill chains.

Do not point this project at production clusters or third-party infrastructure.

## Entry Point

Run everything from the repository root:

```bash
./start.sh
```

`start.sh` is the real entry point. It loads `configuration.conf`, validates the runtime options, builds or reuses `lab-controller:latest`, and starts the controller container named `honeypotlab-controller`.

The controller image is built from:

```text
src/lab/lab-controller/Dockerfile
```

The controller startup flow is:

```text
start.sh
  -> configuration.conf
  -> src/lab/lab-controller/Dockerfile
  -> src/lab/lab-controller/entrypoint.py
  -> /app/start_lab.py
  -> /app/pipeline/*.py
  -> /start_controller.py
```

## What The Controller Does

Inside `honeypotlab-controller`, `entrypoint.py` prepares Docker access, always runs `/app/start_lab.py`, then runs `/start_controller.py` and idles until shutdown.

`/app/start_lab.py` loads the target configuration from:

```text
src/opentelemetry/conf-files/variables.py
```

It then overlays runtime values from `configuration.conf` and executes the Python pipeline in lexical order from:

```text
src/lab/lab-controller/app/pipeline
```

The pipeline currently performs these phases:

- Validate the selected target layout and required files.
- Render `conf-files/kind-cluster.yaml.tmpl` with target variables and create or reuse the Kind cluster.
- Build Docker Compose services and Skaffold artifacts declared by the rendered configuration; Skaffold build uses a temporary Docker-in-Docker helper when artifacts exist.
- Start the target Docker Compose stack from `conf-files/compose.yaml`.
- Deploy the target Skaffold stack from `conf-files/skaffold.yaml.tmpl`.
- Run target-specific pipeline hooks from `src/<target>/hooks/pipeline`.

After the lab pipeline completes, the entrypoint starts:

```text
/start_controller.py
```

That process connects to Caldera when the target provides it, discovers adversaries from `src/<target>/caldera/adversaries`, waits for the required agents, runs the discovered kill chains, calls target-specific hooks from `src/<target>/hooks/controller`, and writes the kill-chain summary.

## Image Build And Deploy Flow

Image builds are target-defined rather than inferred from every directory:

- Underlay images are the services with `build:` entries in `src/opentelemetry/conf-files/compose.yaml`.
- Cluster images are the explicit Skaffold artifacts in `src/opentelemetry/conf-files/skaffold.yaml.tmpl`.

Image names and Dockerfile locations come from those files. For example, `cart` is built by Skaffold with `context: containers/cart` and `dockerfile: src/Dockerfile`, while the three PostgreSQL images reuse `containers/postgres` with different build arguments.

The Compose build uses the controller Docker daemon. The cluster build starts a temporary helper container named `cluster-build-helper`, keeps its layer cache under `res/cache/controller/build-helper`, builds and pushes with Skaffold, then removes the helper container.

Generated runtime deployment files are written under `res/runtime/generated`:

- `skaffold.yaml` for cluster image build and Helm release deploy
- `skaffold-build-artifacts.json` for deploys that reuse the images just built

## Caldera And Kill Chains

Caldera is built from the shared `src/common/caldera` image context and mounts the target Caldera assets from:

```text
src/opentelemetry/caldera/
```

The controller runs every `.yml` or `.yaml` adversary file found in:

```text
src/opentelemetry/caldera/adversaries
```

Files are processed in filename order. By default `KC0` and `KC1` use the Caldera `cluster` group, while the later kill chains use the `outside` group.

## Default Local Services

The default `configuration.conf` uses `HOST_SOCKET=true`, so the controller uses the host Docker socket and host networking. With this mode, `EXPOSE_TO_HOST=true` is effectively satisfied through host networking and the managed port-forward.

Expected local endpoints:

- Astronomy Shop frontend: <http://localhost:8080>
- Caldera: <http://localhost:8888>
- Local registry: `127.0.0.1:5000` on the host and `registry:5000` inside Docker/KinD networking

Caldera credentials are local lab credentials from the mounted Caldera config:

- `admin / admin`
- `red / admin`
- `blue / admin`

## Results

Runtime output is written under:

```text
res/results
```

Important files and directories include:

- `res/results/lab-state.json`
- `res/results/killchain-summary.json`
- `res/results/KC*`
- `res/results/caldera`
- `res/results/kube_events`
- `res/results/telemetry`
- `res/results/frontend-port-forward.log`

Cache and generated runtime data are kept under:

```text
res/cache
res/runtime
```

## Quick Verification

After `./start.sh` has completed the pipeline and the controller is still running:

```bash
docker ps
docker exec honeypotlab-controller kind get clusters
docker exec honeypotlab-controller kubectl --context kind-honeypotlab get pods -A
docker exec honeypotlab-controller kubectl --context kind-honeypotlab get svc -A
docker exec honeypotlab-controller helm --kube-context kind-honeypotlab list -A
curl http://localhost:8080
curl http://localhost:8888
```

`FOLLOW_CONTROLLER_LOGS=true` keeps `./start.sh` attached to the controller logs. Pressing `Ctrl-C` stops log following, not the already running controller container.

## Configuration Notes

Most user-facing options live in:

```text
configuration.conf
```

Target defaults live in:

```text
src/opentelemetry/conf-files/variables.py
```

Notable effective options:

- `CLUSTER_TARGET=opentelemetry`
- `CLUSTER_PROFILE=honeypotlab`
- `BUILD_CONTROLLER=true`
- `FOLLOW_CONTROLLER_LOGS=false`
- `HOST_SOCKET=true`
- `EXPOSE_TO_HOST=true`
- `CALDERA_ADVERSARIES_DIR=/workdir/code/caldera/adversaries`
- `CALDERA_SERVER_ENABLE=true`
- `ATTACKER_ENABLE=true`

The pipeline is always started by the entrypoint. Cleanup is always performed when the controller exits, and the entrypoint itself keeps the container alive after `/start_controller.py` completes.

## Repository Map

```text
start.sh
remove_all.sh
configuration.conf
src/lab/lab-controller/
src/lab/lab-controller/app/start_lab.py
src/lab/lab-controller/app/pipeline/
src/lab/lab-controller/start_controller.py
src/opentelemetry/hooks/pipeline/
src/opentelemetry/hooks/controller/
src/opentelemetry/conf-files/variables.py
src/opentelemetry/caldera/
src/opentelemetry/helm-charts/
src/opentelemetry/containers/
src/common/
res/results/
```

The active controller implementation is under `src/lab/lab-controller`. The target tree mounted into the controller is `src/<CLUSTER_TARGET>`, so with the default configuration that is `src/opentelemetry`.

Target `compose.yaml` files are executed directly through Docker Compose. Skaffold templates are rendered under `res/runtime/generated` before build/deploy.

## Cleanup

Remove the lab controller container:

```bash
./remove_all.sh
```

`remove_all.sh` loads `configuration.conf`, resolves the same controller container name used by `start.sh`, and removes that container if it exists. With the default configuration this is:

```text
honeypotlab-controller
```

The script is safe to run when the controller container is already absent; it reports that there is nothing to remove and exits cleanly. It requires Docker to be installed and the Docker daemon to be reachable, and it exits with an error if Docker cannot remove an existing controller container.

The cleanup is intentionally scoped to the controller container. It does not delete the KinD cluster, local registry data, generated runtime files, results, or image caches. The Python pipeline can reuse those resources on later runs.

To verify cleanup:

```bash
docker ps -a --filter "name=^/honeypotlab-controller$" --format '{{.Names}}'
```

The command should print nothing after `./remove_all.sh` has removed the default controller container.

For a deeper manual reset, remove the KinD cluster and Compose containers with Docker/KinD commands appropriate for your local environment.
