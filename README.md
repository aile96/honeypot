# Kubernetes Honeypot & Adversary Emulation Platform (KinD/Minikube)

> Last update: 2026-02-16  
> **For isolated lab use only.** This platform runs *simulated* attacks via MITRE Caldera for research, training and defensive validation. **Do not use on production systems or third‑party infrastructure.**
>
> **Safety check:** verify your current `kubectl` context (`kubectl config current-context`) points to a local lab cluster (KinD/Minikube). The pipeline operates on whatever cluster is reachable and may patch control-plane/worker components depending on enabled vulnerability switches.

## Table of Contents
- [Overview](#overview)
- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Configuration](#configuration)
- [Quickstart](#quickstart)
- [Service Access](#service-access)
- [Managing Kill Chains (MITRE Caldera)](#managing-kill-chains-mitre-caldera)
- [Cluster Verification](#cluster-verification)
- [Troubleshooting](#troubleshooting)
- [Teardown / Cleanup](#teardown--cleanup)
- [Security & Cost Notes](#security--cost-notes)
- [Appendix: Key Variables](#appendix-key-variables)

---

## Overview

This thesis delivers an **automated platform** that **builds, deploys and tests** a **vulnerable Kubernetes cluster** on a single host using **KinD (Kubernetes in Docker)** or **Minikube**. On top of the cluster, it deploys a **microservices** application (an adapted **OpenTelemetry Astronomy Shop**) with **toggleable vulnerabilities**, together with an **observability stack** (Prometheus, Grafana, Jaeger, OpenTelemetry Collector, optionally OpenSearch).

The platform **orchestrates kill chains** with **MITRE Caldera** (adversary emulation), mapped to **MITRE ATT&CK**, enabling **repeatable** attack/defense experiments and practical **CTI** generation.

**Single entry point:** `./start.sh` runs a **6‑stage pipeline**:
1. **Ensure deps** (`pb/scripts/00_ensure_deps.sh`) – basic tooling/context checks.
2. **Cluster** (`pb/scripts/01_kind_cluster.sh`) – create a local cluster (KinD/Minikube) **only if** none is reachable via `kubectl`.
3. **Underlay setup** (`pb/scripts/02_setup_underlay.sh`) – generate registry CA/credentials, compute a MetalLB IP, and apply cluster‑level switches (e.g., kubelet RO port, etcd exposure, anonymous auth).
4. **Underlay run** (`pb/scripts/03_run_underlay.sh`) – start supporting Docker containers (registry, proxy, Caldera server/controller, attacker, Samba, load generator).
5. **Build & deploy** (`pb/scripts/04_build_deploy.sh`) – build images, push to the local registry, deploy Helm charts (additions, telemetry, Astronomy Shop).
6. **K8s setup** (`pb/scripts/05_setup_k8s.sh`) – create ServiceAccount/kubeconfig for the controller and apply selected DNS/namespace policies.

A full cleanup script is provided: `./remove_all.sh`.

---

## Architecture

```
+-------------------------------- Host (Docker) --------------------------------+
|                                                                                |
|  Reverse Proxy (:8080)      Local Registry (:5000)        Caldera (:8888)     |
|  Samba/CSI helper           Caldera Controller            Attacker             |
|  Load Generator (Locust)    ...                                               |
|                                                                                |
|     \\                                                                    |
|      \\__ shared Docker network __________________________________________|
|                         |                                      |              |
|                         v                                      v              |
|                  +---------------- KinD/Minikube ----------------+            |
|                  |  Control Plane + N workers (WORKERS)          |            |
|                  |  Namespaces: app, dat, dmz, mem, pay, tst     |            |
|                  |                                               |            |
|                  |  Astronomy Shop (microservices)               |            |
|                  |  Helm charts: additions / telemetry / app     |            |
|                  +-----------------------------------------------+            |
+--------------------------------------------------------------------------------+
```

**Key components**:
- **Kubernetes cluster**: KinD or Minikube. Current config default is **Minikube** (`KIND_CLUSTER=0`), while KinD is generally recommended. Worker count configurable via `WORKERS`.
- **Application**: *Astronomy Shop* microservices with optional vulnerabilities (e.g., `dnsGrant`, `deployGrant`, `anonymousGrant`, `currencyGrant`) and **NetworkPolicy** toggles.
- **Telemetry**: OTel Collector, Prometheus, Grafana, Jaeger, (opt.) OpenSearch. **Open** or **protected** mode (`LOG_OPEN`).
- **Underlay** (supporting containers on the Docker host):
  - **Local registry** (default `registry:5000`, Docker-network only; used for image pushes).
  - **Reverse proxy** exposing the **app frontend** on `:8080` and **Caldera** on `:8888`.
  - **MITRE Caldera** server and **controller** (auto‑starting operations).
  - **Attacker** (kill chain scripts) and **Load Generator** (Locust).
  - **Samba** for CSI SMB (persistent volumes).

**Repo map** (where to look):
- Pipeline scripts: `pb/scripts/*`
- Underlay containers & Caldera content: `pb/docker/*`
- Helm charts: `helm-charts/{additions,telemetry,astronomy-shop}`
- Service sources and custom images: `src/*`

---

## Prerequisites

- **OS**: Linux or macOS (x86_64/arm64). On Windows, use WSL2.
- **Minimum suggested resources**: 4 CPU, 12-16 GB RAM, 80+ GB free disk.
- **Cluster size requirement**: at least **3 nodes total** (control-plane + ≥2 workers). Keep `WORKERS>=2`.
- **Software** (available in PATH):
  - **Docker** (Engine/Desktop) running
  - **kubectl** (≥ 1.28; cluster defaults to K8s 1.30.x)
  - **kind** (≥ 0.22) **or** **minikube** (≥ 1.33), required when `./start.sh` needs to create a cluster
  - **helm** (≥ 3.12)
  - `docker buildx` (Buildx plugin)
  - `htpasswd` (from `apache2-utils` / `httpd-tools`)
  - **jq**, **curl**, **openssl**
- **Open ports** (defaults): `8080` (frontend proxy), `8888` (Caldera proxy).

> The script `pb/scripts/00_ensure_deps.sh` performs basic checks and fails with clear messages if something is missing.
> If a cluster is already reachable via `kubectl`, `pb/scripts/01_kind_cluster.sh` skips cluster creation.

---

## Configuration

Most options live in **`configuration.conf`** (loaded by `./start.sh`); some advanced values are computed at runtime or have script defaults. Key examples:

### Cluster choice
- `KIND_CLUSTER=1` to use **KinD**.  
  If `KIND_CLUSTER=0`, target is **Minikube** (**default in current `configuration.conf`**).
- `K8S_VERSION=1.30.0` cluster version (**optional in `configuration.conf`**; default from `pb/scripts/01_kind_cluster.sh`).
- `WORKERS=2` number of workers (must be ≥2, **optional in `configuration.conf`**; default from `pb/scripts/01_kind_cluster.sh`).  
  (Minikube creates `WORKERS+1` total nodes; KinD creates `WORKERS` workers + 1 control-plane.)

### Image registry
- `REGISTRY_NAME=registry` – helper/hostname.
- `REGISTRY_PORT=5000` – registry port (inside the Docker network).
- `REGISTRY_USER`, `REGISTRY_PASS` – credentials.

### Telemetry
- `LOG_OPEN=true|false` – selects *noauth*/**auth** values for the `telemetry` chart.
- `LOG_TOKEN=true|false` – enables/disables synthetic token log generation (used by some scenarios).
- Dashboards & collectors are deployed automatically (Grafana, Jaeger, Prometheus, OTel Collector, opt. OpenSearch).

### Vulnerabilities (chart `additions` → `values.yaml`)
- `DNS_GRANT=true|false`
- `DEPLOY_GRANT=true|false`
- `ANONYMOUS_GRANT=true|false`
- `CURRENCY_GRANT=true|false`
- **NetworkPolicy**: enable/disable default deny and selected exceptions (see `helm-charts/additions/values.yaml`).

Note: `ANONYMOUS_GRANT` is effective only when `ANONYMOUS_AUTH=true` (see below).

### Cluster-level toggles (scripts)
- `OPEN_PORTS=true|false` – enables kubelet read-only port (`10255`) on worker nodes.
- `ETCD_EXPOSURE=true|false` – exposes etcd client port (`12379`) on the control-plane.
- `ANONYMOUS_AUTH=true|false` – enables Kubernetes API anonymous authentication (`--anonymous-auth=true`).
- `RECURSIVE_DNS=true|false` – toggles DNS recursion behavior (CoreDNS changes in `pb/scripts/05_setup_k8s.sh`).
- `MISSING_POLICY=true|false` – if `true`, skips Pod Security labels (less restricted namespaces).

### Underlay services (Docker containers)
Enable/disable optional underlay containers started by `pb/scripts/03_run_underlay.sh`:
- `registry` is started unconditionally (name/port controlled by `REGISTRY_NAME`, `REGISTRY_PORT`).
- `PROXY_ENABLE=true|false`
- `CALDERA_SERVER_ENABLE=true|false`
- `CALDERA_CONTROLLER_ENABLE=true|false`
- `ATTACKER_ENABLE=true|false`
- `SAMBA_ENABLE=true|false`
- `LOAD_GENERATOR_ENABLE=true|false`

### Kill chains (MITRE Caldera)
- `ADV_LIST="KC1 – Image@cluster, KC2 – WiFi@outside, ..."` – order and **agent group** (`cluster`/`outside`).  
  Alternatively, `ADV_NAME="KC0 – Test"`.
- Enable flags: `ENABLEKC1=true`, …, `ENABLEKC6=true`.
- Hooks: `SCRIPT_PRE_KC*` / `SCRIPT_POST_KC*` for pre/post steps.
- Abilities & adversaries live under `pb/docker/caldera/abilities/` and `.../adversaries/`.
- KCxxyy: Script to run for each step yy of kill chain number xx.

### Namespace names (defaults)
```
APP_NAMESPACE=app
DAT_NAMESPACE=dat
DMZ_NAMESPACE=dmz
MEM_NAMESPACE=mem
PAY_NAMESPACE=pay
TST_NAMESPACE=tst
```

---

## Quickstart

1. **Clone or extract** the repo and `cd` to the root (where `start.sh` lives).  
2. **(Optional)** Edit `configuration.conf` to fit your scenario (cluster, vulnerabilities, kill chains steps, telemetry).  
3. **Run the pipeline**:
   ```bash
   ./start.sh
   ```
   It executes in order:
   - `00_ensure_deps.sh`
   - `01_kind_cluster.sh`
   - `02_setup_underlay.sh`
   - `03_run_underlay.sh`
   - `04_build_deploy.sh`
   - `05_setup_k8s.sh`

When it finishes you’ll see **“Pipeline completed”**.

### Fast path (default config)
If you want to boot the lab with defaults and verify quickly:
```bash
kubectl config current-context
./start.sh
kubectl get nodes && kubectl get pods -A
```
Then open:
- <http://localhost:8080/> (app front-end)
- <http://localhost:8888> (Caldera)

> The very first run may take a while (image builds & chart pulls). Subsequent runs benefit from caching.

---

## Service Access

- **App front‑end**: <http://localhost:8080/>  
  (the *Astronomy Shop* UI).  
- **MITRE Caldera UI**: <http://localhost:8888>  
  Default (lab‑only) users from `local.yml`:  
  - `admin / admin`  
  - `red / admin`  
  - `blue / admin`  
- **Local registry**: `registry:5000` (used internally for image pushes).

> Some telemetry endpoints (Grafana/Jaeger/Prometheus/OpenSearch) are **in‑cluster**; use `kubectl port-forward` unless already published via the proxy.

---

## Managing Kill Chains (MITRE Caldera)

- **Auto‑start**: the **caldera-controller** container reads `ADV_LIST` / `ENABLEKC*` and creates **Operations** on Caldera, mapping agent groups (`@cluster`, `@outside`).  
- **Monitoring**: use Caldera UI → Operations to follow progress. Chains are sequences of steps mapped to ATT&CK TTPs.
- **Customization**: change `configuration.conf` (e.g., disable `ENABLEKC3=false`) or update files under `pb/docker/caldera/abilities/*` and `.../adversaries/*`.
- **Agents**: agents can run **inside** the cluster (e.g., sidecars) or **outside** (the “attacker” container).

> All activity is **simulated** and confined to the lab environment.

---

## Cluster Verification

Handy commands:
```bash
kubectl get nodes
kubectl get pods -A
kubectl get svc -A
helm ls -A
```
Check that pods in application namespaces (`app`, `dat`, `dmz`, `mem`, `pay`, `tst`) and telemetry namespaces are **Running/Ready**.  
Inspect logs for problematic pods:
```bash
kubectl -n <NAMESPACE> logs <POD> --all-containers=true --tail=200
```

---

## Troubleshooting

**Docker not running / permissions**  
Ensure Docker is running and your user can run `docker` without `sudo` (Linux: add user to `docker` group).

**Port conflicts (8080/8888)**  
Stop conflicting processes or change port mappings/templates in:
- `pb/scripts/03_run_underlay.sh` (Docker `-p` mappings)
- `pb/docker/proxy/nginx.conf.template` (`listen 8080` / `listen 8888`)

**Image pull issues with the registry**  
The registry runs inside the Docker network and uses TLS + basic auth by default. If pushes/pulls fail, inspect the `registry` container logs and the CA/auth distribution step in `pb/scripts/02_setup_underlay.sh`.

**Insufficient memory / OOM**  
Lower `WORKERS` (but keep `WORKERS>=2`), disable chains or non‑essential components, or allocate more RAM to the Docker VM.

**Minikube driver**  
When using Minikube, ensure it uses the **Docker driver** (nodes must be visible as Docker containers). Several scripts patch nodes via `docker exec` and will fail with VM drivers.

**Helm/Repos**  
If a chart can’t be resolved, run `helm repo update` and retry.

**Caldera unreachable**  
Inspect the **proxy** and **caldera-server** containers (`docker ps`, `docker logs <name>`).

---

## Teardown / Cleanup

To remove **everything** (helper containers, cluster):
```bash
./remove_all.sh
```
> `remove_all.sh` deletes both KinD and Minikube clusters (if present) and removes Docker networks named `kind`/`minikube`.

Alternatively:
```bash
kind delete cluster     # if using Kind
minikube delete         # if using Minikube
docker rm -f ...        # all docker supporting network
```

---

## Security & Cost Notes

- The platform is **LAB‑only**. Do not expose services beyond localhost.  
- **Default passwords** (e.g., Caldera) are intentionally weak for tests: **change them** if needed.  
- On paid cloud/VMs, telemetry (Prometheus, Grafana, Jaeger, OpenSearch) may be resource‑intensive: consider retention/limits.

---

## Appendix: Key Variables

> The following variables are sourced from `configuration.conf` and/or from script defaults/runtime exports in `pb/scripts/*.sh`.

- **Cluster**: `KIND_CLUSTER`, `WORKERS`, `K8S_VERSION`
- **Registry**: `REGISTRY_NAME`, `REGISTRY_PORT`, `REGISTRY_USER`, `REGISTRY_PASS`
- **Proxy/Service**: `PROXY`, `CALDERA_SERVER`, `CALDERA_CONTROLLER`, `ATTACKER`, `GENERIC_SVC_PORT`
- **Telemetry**: `LOG_OPEN`, `LOG_TOKEN`
- **Kill chains**: `ADV_LIST`, `ADV_NAME`, `ENABLEKC1..6`, `SCRIPT_PRE_KC*`, `SCRIPT_POST_KC*`
- **Vulnerabilities (Helm additions)**: `DNS_GRANT`, `DEPLOY_GRANT`, `ANONYMOUS_GRANT`, `CURRENCY_GRANT`
- **Cluster-level toggles**: `OPEN_PORTS`, `ETCD_EXPOSURE`, `ANONYMOUS_AUTH`, `RECURSIVE_DNS`, `MISSING_POLICY`
- **Underlay services**: `*_ENABLE` flags (e.g., `PROXY_ENABLE`, `CALDERA_SERVER_ENABLE`, `ATTACKER_ENABLE`, ...)
- **Namespaces**: `APP_NAMESPACE`, `DAT_NAMESPACE`, `DMZ_NAMESPACE`, `MEM_NAMESPACE`, `PAY_NAMESPACE`, `TST_NAMESPACE`
- **Script/runtime derived or optional overrides**: `FRONTEND_PROXY_IP`, `INTERNAL_REGISTRY`, `INSECURE_REGISTRY`

---

### Helpful references

- Platform based on the Astronomy Shop of Open Telemetry:
    Documentation: https://opentelemetry.io/docs/demo/architecture/
    Git repository: https://github.com/open-telemetry/opentelemetry-demo/tree/main
- Pipeline scripts: `pb/scripts/00_ensure_deps.sh` … `05_setup_k8s.sh`
- Helm charts: `helm-charts/additions`, `helm-charts/telemetry`, `helm-charts/astronomy-shop`
- MITRE Caldera:
  - Config: `pb/docker/caldera/local.yml` (ports, API keys, “red/blue” users)
  - Abilities & adversaries: `pb/docker/caldera/abilities/*`, `.../adversaries/*`
- App & helpers sources: `src/*` (microservices, attacker, caldera-controller, samba, load-generator)
