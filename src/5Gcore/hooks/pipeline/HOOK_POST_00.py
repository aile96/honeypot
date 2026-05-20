#!/usr/bin/env python3
"""Prepare 5Gcore runtime assets before the Compose underlay starts.

This hook is executed after the validation step and before Docker Compose is
started by the generic pipeline. It creates the HTTPS registry credentials,
generates the registry CA/server certificate, installs the CA in the controller,
and writes the containerd certs.d configuration that Kind nodes will mount for
authenticated HTTPS image pulls."""

import base64
import shutil
from pathlib import Path

from lib import (
    config_bool,
    config_str,
    die,
    docker_bind_source,
    log,
    registry_endpoint,
    require_config,
    run_cmd,
    set_config_default,
    set_state_value,
    warn,
)

DEFAULT_RELATIVE_DIRS = {
    "RESULTS_DIR": "results",
    "STATE_DIR": "state",
    "GENERATED_DIR": "generated",
}


def require_registry_credentials() -> tuple[str, str]:
    """Return registry credentials, failing early when they are missing."""
    username = config_str(CONFIG, "REGISTRY_USER", "", allow_empty=True).strip()
    password = config_str(CONFIG, "REGISTRY_PASS", "", allow_empty=True).strip()

    if not username or not password:
        die("REGISTRY_USER and REGISTRY_PASS must be set for the authenticated lab registry.")

    return username, password


def registry_scheme() -> str:
    """Return the fixed local registry scheme."""
    return "https"


def generate_registry_htpasswd(auth_dir: Path, username: str, password: str) -> Path:
    """Generate the htpasswd file consumed by registry:2."""
    auth_dir.mkdir(parents=True, exist_ok=True)
    htpasswd_path = auth_dir / "htpasswd"
    completed = run_cmd(["htpasswd", "-Bbn", username, password], capture_output=True, config=CONFIG)
    htpasswd_path.write_text(completed.stdout, encoding="utf-8")
    return htpasswd_path


def generate_registry_tls(certs_dir: Path) -> dict[str, str]:
    """Generate a root CA and registry server certificate for HTTPS registry access."""
    registry_name = config_str(CONFIG, "REGISTRY_NAME", "registry", allow_empty=False)
    certs_dir.mkdir(parents=True, exist_ok=True)
    root_crt = certs_dir / "rootca.crt"
    root_key = certs_dir / "rootca.key"
    domain_crt = certs_dir / "domain.crt"
    domain_key = certs_dir / "domain.key"

    if not root_crt.is_file() or not root_key.is_file():
        log("Generating registry root CA.")
        run_cmd(["openssl", "genrsa", "-out", str(root_key), "4096"], config=CONFIG)
        run_cmd(
            [
                "openssl", "req", "-x509", "-new", "-nodes",
                "-key", str(root_key), "-sha256", "-days", "3650",
                "-subj", "/CN=registry-rootca",
                "-addext", "basicConstraints=critical,CA:true",
                "-addext", "keyUsage=critical,keyCertSign,cRLSign",
                "-out", str(root_crt),
            ],
            config=CONFIG,
        )
    else:
        warn("Registry root CA already exists; leaving it in place.")

    if not domain_crt.is_file() or not domain_key.is_file():
        log("Generating registry TLS certificate.")
        csr = certs_dir / "domain.csr"
        ext = certs_dir / "domain.ext"
        run_cmd(["openssl", "genrsa", "-out", str(domain_key), "4096"], config=CONFIG)
        run_cmd(
            ["openssl", "req", "-new", "-key", str(domain_key), "-subj", f"/CN={registry_name}", "-out", str(csr)],
            config=CONFIG,
        )
        ext.write_text(
            "\n".join(
                [
                    f"subjectAltName=DNS:{registry_name}",
                    "extendedKeyUsage=serverAuth",
                    "keyUsage=digitalSignature,keyEncipherment",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        run_cmd(
            [
                "openssl", "x509", "-req", "-in", str(csr),
                "-CA", str(root_crt), "-CAkey", str(root_key), "-CAcreateserial",
                "-out", str(domain_crt), "-days", "825", "-sha256", "-extfile", str(ext),
            ],
            config=CONFIG,
        )
        csr.unlink(missing_ok=True)
        ext.unlink(missing_ok=True)
        (certs_dir / "rootca.srl").unlink(missing_ok=True)
    else:
        warn("Registry TLS certificate already exists; leaving it in place.")

    CONFIG["REGISTRY_CA_FILE"] = str(root_crt)
    CONFIG["REGISTRY_SCHEME"] = registry_scheme()
    return {"root_ca": str(root_crt), "server_certificate": str(domain_crt), "server_key": str(domain_key)}


def install_registry_ca_in_controller(ca_file: Path) -> None:
    """Trust the generated registry CA in the controller."""
    if not ca_file.is_file():
        warn(f"Registry CA file not found for controller trust install: {ca_file}")
        return

    hostport = registry_endpoint(CONFIG)
    registry_name = config_str(CONFIG, "REGISTRY_NAME", "registry", allow_empty=False)
    docker_cert_dir = Path("/etc/docker/certs.d") / hostport
    system_ca = Path("/usr/local/share/ca-certificates") / f"registry-{registry_name}.crt"
    docker_cert_dir.mkdir(parents=True, exist_ok=True)
    system_ca.parent.mkdir(parents=True, exist_ok=True)
    (docker_cert_dir / "ca.crt").write_bytes(ca_file.read_bytes())
    system_ca.write_bytes(ca_file.read_bytes())
    run_cmd(["update-ca-certificates"], check=False, quiet=True, config=CONFIG)
    log(f"Installed registry CA in controller trust stores for {hostport}.")


def prepare_registry_assets() -> dict[str, object]:
    """Prepare per-run registry auth/TLS files and per-lab registry storage."""
    username, password = require_registry_credentials()
    scheme = registry_scheme()

    runtime_dir = Path(config_str(CONFIG, "RUNTIME_DIR", "/res/runtime"))
    auth_dir = Path(config_str(CONFIG, "REGISTRY_AUTH_DIR", str(runtime_dir / "registry")))
    if str(auth_dir) == "/res/runtime/registry":
        auth_dir = runtime_dir / "registry"
    CONFIG["REGISTRY_AUTH_DIR"] = str(auth_dir)
    certs_dir = auth_dir / "certs"
    certs_dir.mkdir(parents=True, exist_ok=True)
    (auth_dir / "storage").mkdir(parents=True, exist_ok=True)

    htpasswd_path = generate_registry_htpasswd(auth_dir, username, password)
    tls_assets = generate_registry_tls(certs_dir)
    install_registry_ca_in_controller(Path(tls_assets["root_ca"]))

    CONFIG["COMPOSE_REGISTRY_AUTH_DIR"] = str(docker_bind_source(auth_dir, CONFIG))
    CONFIG["COMPOSE_REGISTRY_CERTS_DIR"] = str(docker_bind_source(certs_dir, CONFIG))
    CONFIG["COMPOSE_REGISTRY_STORAGE_DIR"] = str(docker_bind_source(auth_dir / "storage", CONFIG))
    CONFIG["COMPOSE_REGISTRY_TLS_CERTIFICATE"] = "/auth/certs/domain.crt"
    CONFIG["COMPOSE_REGISTRY_TLS_KEY"] = "/auth/certs/domain.key"

    return {
        "auth_dir": str(auth_dir),
        "certs_dir": str(certs_dir),
        "htpasswd": str(htpasswd_path),
        "endpoint": registry_endpoint(CONFIG),
        "scheme": scheme,
        "tls": tls_assets,
        "image_storage": CONFIG["COMPOSE_REGISTRY_STORAGE_DIR"],
    }


def prepare_kind_containerd_registry_config() -> dict[str, str]:
    """Prepare /etc/containerd/certs.d config to mount into Kind nodes."""
    scheme = registry_scheme()
    hostport = registry_endpoint(CONFIG)
    username, password = require_registry_credentials()
    auth_b64 = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")

    runtime_dir = Path(config_str(CONFIG, "RUNTIME_DIR", "/res/runtime"))
    auth_dir = Path(config_str(CONFIG, "REGISTRY_AUTH_DIR", str(runtime_dir / "registry")))
    if str(auth_dir) == "/res/runtime/registry":
        auth_dir = runtime_dir / "registry"
    CONFIG["REGISTRY_AUTH_DIR"] = str(auth_dir)
    root_dir = auth_dir / "containerd-certs.d"
    registry_dir = root_dir / hostport
    registry_dir.mkdir(parents=True, exist_ok=True)

    ca_file = Path(config_str(CONFIG, "REGISTRY_CA_FILE", str(auth_dir / "certs" / "rootca.crt")))
    if not ca_file.is_file():
        die(f"Registry CA file not found: {ca_file}")

    shutil.copyfile(ca_file, registry_dir / "ca.crt")

    hosts_toml = registry_dir / "hosts.toml"
    hosts_toml.write_text(
        "\n".join(
            [
                f'server = "{scheme}://{hostport}"',
                "",
                f'[host."{scheme}://{hostport}"]',
                'capabilities = ["pull", "resolve"]',
                f'ca = "/etc/containerd/certs.d/{hostport}/ca.crt"',
                "",
                f'[host."{scheme}://{hostport}".header]',
                f'Authorization = "Basic {auth_b64}"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    CONFIG["KIND_CONTAINERD_CERTS_DIR"] = str(docker_bind_source(root_dir, CONFIG))

    return {
        "root_dir": str(root_dir),
        "registry_dir": str(registry_dir),
        "hosts_toml": str(hosts_toml),
        "mounted_host_path": CONFIG["KIND_CONTAINERD_CERTS_DIR"],
        "registry": hostport,
        "scheme": scheme,
    }


def prepare_attacker_compose_inputs(runtime_dir: Path) -> dict[str, str]:
    """Create mutable attacker files populated by later Kind discovery hooks."""
    attacker_dir = runtime_dir / "attacker"
    attacker_dir.mkdir(parents=True, exist_ok=True)

    env_file = attacker_dir / "attacker.env"
    env_file.write_text("", encoding="utf-8")

    iphost_file = attacker_dir / "iphost"
    if iphost_file.is_dir():
        shutil.rmtree(iphost_file)
    iphost_file.write_text("", encoding="utf-8")

    apiserver_dir = attacker_dir / "apiserver"
    if apiserver_dir.exists() and not apiserver_dir.is_dir():
        apiserver_dir.unlink()
    apiserver_dir.mkdir(parents=True, exist_ok=True)

    CONFIG["COMPOSE_ATTACKER_ENV_FILE"] = str(env_file)
    CONFIG["COMPOSE_ATTACKER_IPHOST_FILE"] = str(docker_bind_source(iphost_file, CONFIG))
    CONFIG["COMPOSE_ATTACKER_APISERVER_DIR"] = str(docker_bind_source(apiserver_dir, CONFIG))

    return {
        "env_file": str(env_file),
        "iphost_file": str(iphost_file),
        "apiserver_dir": str(apiserver_dir),
    }


def main() -> None:
    code_root = Path(str(require_config(CONFIG, "CODE_ROOT")))
    runtime_dir = Path(config_str(CONFIG, "RUNTIME_DIR", "/res/runtime"))
    created: dict[str, str] = {}

    for key, relative in DEFAULT_RELATIVE_DIRS.items():
        if key == "GENERATED_DIR":
            default_path = runtime_dir / "generated"
        else:
            default_path = code_root / relative
        path = Path(str(set_config_default(CONFIG, key, str(default_path))))
        path.mkdir(parents=True, exist_ok=True)
        created[key] = str(path)

    registry_assets = prepare_registry_assets()
    kind_registry_config = prepare_kind_containerd_registry_config()
    attacker_compose_inputs = prepare_attacker_compose_inputs(runtime_dir)

    lab_name = config_str(CONFIG, "LAB_NAME", config_str(CONFIG, "CLUSTER_PROFILE", "honeypotlab"), allow_empty=False)
    CONFIG["COMPOSE_PROJECT_NAME"] = config_str(CONFIG, "COMPOSE_PROJECT_NAME", f"honeypot-{lab_name}")
    CONFIG["CP_NETWORK"] = config_str(CONFIG, "CP_NETWORK", f"kind-{lab_name}")
    CONFIG["COMPOSE_PORT_BIND_ADDR"] = "0.0.0.0"
    compose_services = ["registry"]
    compose_build_services: list[str] = []
    if config_bool(CONFIG, "CALDERA_SERVER_ENABLE", True):
        compose_services.append("caldera")
        compose_build_services.append("caldera")

    if config_bool(CONFIG, "ATTACKER_ENABLE", True):
        compose_services.append("attacker")
        compose_build_services.append("attacker")

    if config_bool(CONFIG, "SAMBA_ENABLE", True):
        compose_services.append("samba")
        compose_build_services.append("samba")

    CONFIG["COMPOSE_BOOTSTRAP_SERVICES"] = ["registry"]
    CONFIG["COMPOSE_SERVICES"] = compose_services
    CONFIG["COMPOSE_DEPLOY_SERVICES"] = compose_services
    CONFIG["COMPOSE_BUILD_SERVICES"] = compose_build_services

    CONFIG["COMPOSE_FREE5GC_CERT_DIR"] = str(
        docker_bind_source(code_root / "helm-charts" / "free5gc" / "cert", CONFIG)
    )

    caldera_root = code_root / "caldera"
    CONFIG["COMPOSE_CALDERA_LOCAL_YML"] = str(docker_bind_source(caldera_root / "local.yml", CONFIG))
    CONFIG["COMPOSE_CALDERA_ABILITIES_DIR"] = str(docker_bind_source(caldera_root / "abilities", CONFIG))
    CONFIG["COMPOSE_CALDERA_ADVERSARIES_DIR"] = str(docker_bind_source(caldera_root / "adversaries", CONFIG))

    set_state_value(STATE, "prepared_directories", created)
    set_state_value(STATE, "registry_assets", registry_assets)
    set_state_value(STATE, "kind_containerd_registry_config", kind_registry_config)
    set_state_value(STATE, "attacker_compose_inputs", attacker_compose_inputs)
    set_state_value(STATE, "compose_services", compose_services)
    set_state_value(STATE, "compose_build_services", compose_build_services)
    log("5Gcore runtime preparation completed.")


if __name__ == "__main__":
    main()
