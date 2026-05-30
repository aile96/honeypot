#!/usr/bin/env python3
"""Prepare OpenTelemetry runtime assets before Compose starts.

This hook creates target-specific runtime directories, registry credentials,
registry TLS/trust material, Caldera mounts, attacker environment files, and
Kind containerd registry configuration. It prepares inputs for the generic Compose
step without starting services itself."""

import base64
import re
import shutil
from pathlib import Path

from lib import (
    config_bool,
    config_int,
    config_str,
    die,
    docker_bind_source,
    image_version,
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
}

ATTACKER_ENV_PREFIXES = (
    "KC",
)


def env_file_value(value: object) -> str:
    """Quote a value for a simple Docker Compose env_file."""
    text = str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')
    return f'"{text}"'


def bool_value(name: str, default: bool) -> str:
    """Return a CONFIG boolean as a Helm-friendly string."""
    return "true" if config_bool(CONFIG, name, default) else "false"


def bool_text(name: str, default: bool, on_true: str, on_false: str) -> str:
    """Return one of two strings based on a CONFIG boolean."""
    return on_true if config_bool(CONFIG, name, default) else on_false


def bool_all_value(name_a: str, default_a: bool, name_b: str, default_b: bool) -> str:
    """Return true only when both CONFIG booleans are true."""
    return (
        "true"
        if config_bool(CONFIG, name_a, default_a) and config_bool(CONFIG, name_b, default_b)
        else "false"
    )


def set_skaffold_template_values() -> None:
    """Create values that replace the bash template helper functions."""
    CONFIG.setdefault("FRONTEND_PROXY_IP", "172.18.0.200")
    CONFIG.setdefault("GENERIC_SVC_ADDR", "172.18.0.201")
    CONFIG.setdefault("SOCKET_SHARED", True)
    CONFIG.setdefault("CRICTL_RUNTIME_PATH", "/run/containerd/containerd.sock")

    CONFIG["FLAGD_CREDENTIALS_TYPE"] = bool_text("FLAGD_CONFIGMAP", True, "configmap", "secret")
    CONFIG["ANONYMOUS_GRANT_EFFECTIVE"] = bool_all_value(
        "ANONYMOUS_AUTH",
        False,
        "ANONYMOUS_GRANT",
        False,
    )
    CONFIG["LOG_TOKEN_SCRIPT"] = bool_text("LOG_TOKEN", True, "synthetic-log.sh", "null.sh")
    CONFIG["SAMBA_ENABLE_VALUE"] = bool_value("SAMBA_ENABLE", True)
    CONFIG["DNS_GRANT_VALUE"] = bool_value("DNS_GRANT", True)
    CONFIG["DEPLOY_GRANT_VALUE"] = bool_value("DEPLOY_GRANT", True)
    CONFIG["CURRENCY_GRANT_VALUE"] = bool_value("CURRENCY_GRANT", True)
    CONFIG["FLAGD_FEATURES_VALUE"] = bool_value("FLAGD_FEATURES", True)
    CONFIG["AUTO_DEPLOY_VALUE"] = bool_value("AUTO_DEPLOY", True)
    CONFIG["HOST_NETWORK_VALUE"] = bool_value("HOST_NETWORK", True)
    CONFIG["RCE_VULN_VALUE"] = bool_value("RCE_VULN", True)
    CONFIG["TELEMETRY_VALUES_MODE"] = bool_text("LOG_OPEN", True, "noauth", "auth")
    CONFIG["SMTP_SOCKET_PATH"] = bool_text(
        "SOCKET_SHARED",
        True,
        config_str(CONFIG, "CRICTL_RUNTIME_PATH", "/run/containerd/containerd.sock"),
        "/tmp/disabled-containerd.sock",
    )
    CONFIG["SMTP_SOCKET_TYPE"] = bool_text("SOCKET_SHARED", True, "Socket", "FileOrCreate")
    CONFIG["CALDERA_URL"] = f"http://{config_str(CONFIG, 'CALDERA_SERVER', 'caldera')}:8888"

    template_values = {
        key: CONFIG[key]
        for key in (
            "FLAGD_CREDENTIALS_TYPE",
            "ANONYMOUS_GRANT_EFFECTIVE",
            "LOG_TOKEN_SCRIPT",
            "SAMBA_ENABLE_VALUE",
            "DNS_GRANT_VALUE",
            "DEPLOY_GRANT_VALUE",
            "CURRENCY_GRANT_VALUE",
            "FLAGD_FEATURES_VALUE",
            "AUTO_DEPLOY_VALUE",
            "HOST_NETWORK_VALUE",
            "RCE_VULN_VALUE",
            "TELEMETRY_VALUES_MODE",
            "SMTP_SOCKET_PATH",
            "SMTP_SOCKET_TYPE",
            "CALDERA_URL",
            "FRONTEND_PROXY_IP",
            "GENERIC_SVC_ADDR",
        )
    }
    set_state_value(STATE, "skaffold_template_values", template_values)


def ability_tactic(path: Path) -> str:
    """Extract the Caldera tactic from one simple ability YAML file."""
    match = re.search(r"(?m)^\s*tactic:\s*['\"]?([^'\"\s]+)", path.read_text(encoding="utf-8"))
    return match.group(1) if match else "uncategorized"


def prepare_caldera_abilities_mount(caldera_root: Path, generated_dir: Path) -> Path:
    """Stage abilities under tactic-named paths to avoid Caldera wrong-tactic noise."""
    source = caldera_root / "abilities"
    staged = generated_dir / "caldera-abilities"
    if staged.exists():
        shutil.rmtree(staged)
    for source_file in sorted(source.rglob("*.yml")):
        tactic = ability_tactic(source_file)
        destination = staged / tactic / source_file.relative_to(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, destination)
    return staged


def require_registry_credentials() -> tuple[str, str]:
    """Return registry credentials, failing early when they are missing."""
    username = config_str(CONFIG, "REGISTRY_USER", "", allow_empty=True)
    password = config_str(CONFIG, "REGISTRY_PASS", "", allow_empty=True)

    if not username or not password:
        die("REGISTRY_USER and REGISTRY_PASS must be set for the authenticated lab registry.")

    return username, password


def registry_scheme() -> str:
    """Return the fixed local registry scheme."""
    return "https"


def generate_registry_htpasswd(auth_dir: Path, username: str, password: str) -> Path:
    """Generate the htpasswd file consumed by registry:2."""
    htpasswd_path = auth_dir / "htpasswd"
    completed = run_cmd(
        ["htpasswd", "-Bbn", username, password],
        capture_output=True,
        config=CONFIG,
    )
    htpasswd_path.write_text(completed.stdout, encoding="utf-8")
    return htpasswd_path


def generate_registry_tls(certs_dir: Path) -> dict[str, str]:
    """Generate a root CA and registry server certificate when absent."""
    registry_name = config_str(CONFIG, "REGISTRY_NAME", "registry", allow_empty=False)
    root_crt = certs_dir / "rootca.crt"
    root_key = certs_dir / "rootca.key"
    domain_crt = certs_dir / "domain.crt"
    domain_key = certs_dir / "domain.key"

    if not root_crt.is_file() or not root_key.is_file():
        log("Generating registry root CA.")
        run_cmd(["openssl", "genrsa", "-out", str(root_key), "4096"], config=CONFIG)
        run_cmd(
            [
                "openssl",
                "req",
                "-x509",
                "-new",
                "-nodes",
                "-key",
                str(root_key),
                "-sha256",
                "-days",
                "3650",
                "-subj",
                "/CN=registry-rootca",
                "-addext",
                "basicConstraints=critical,CA:true",
                "-addext",
                "keyUsage=critical,keyCertSign,cRLSign",
                "-out",
                str(root_crt),
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
            [
                "openssl",
                "req",
                "-new",
                "-key",
                str(domain_key),
                "-subj",
                f"/CN={registry_name}",
                "-out",
                str(csr),
            ],
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
                "openssl",
                "x509",
                "-req",
                "-in",
                str(csr),
                "-CA",
                str(root_crt),
                "-CAkey",
                str(root_key),
                "-CAcreateserial",
                "-out",
                str(domain_crt),
                "-days",
                "825",
                "-sha256",
                "-extfile",
                str(ext),
            ],
            config=CONFIG,
        )

        csr.unlink(missing_ok=True)
        ext.unlink(missing_ok=True)
        (certs_dir / "rootca.srl").unlink(missing_ok=True)
    else:
        warn("Registry TLS certificate already exists; leaving it in place.")

    CONFIG["REGISTRY_CA_FILE"] = str(root_crt)

    return {
        "root_ca": str(root_crt),
        "server_certificate": str(domain_crt),
        "server_key": str(domain_key),
    }


def install_registry_ca_in_controller(ca_file: Path) -> None:
    """Trust the generated registry CA for Docker CLI calls in the controller."""
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
    """Prepare per-run local registry auth/TLS files and Compose bind paths.

    Registry auth/TLS assets and storage are written under the per-lab runtime
    directory. The shared cross-lab cache is handled by registry-lab.
    """
    username, password = require_registry_credentials()
    scheme = registry_scheme()

    runtime_dir = Path(config_str(CONFIG, "RUNTIME_DIR", "/res/runtime"))
    auth_dir = Path(config_str(CONFIG, "REGISTRY_AUTH_DIR", str(runtime_dir / "registry")))
    if str(auth_dir) == "/res/runtime/registry":
        auth_dir = runtime_dir / "registry"
    CONFIG["REGISTRY_AUTH_DIR"] = str(auth_dir)
    certs_dir = auth_dir / "certs"

    for path in (auth_dir, certs_dir, auth_dir / "storage"):
        path.mkdir(parents=True, exist_ok=True)

    htpasswd_path = generate_registry_htpasswd(auth_dir, username, password)

    tls_assets: dict[str, str] = {}
    if scheme == "https":
        tls_assets = generate_registry_tls(certs_dir)
        install_registry_ca_in_controller(Path(tls_assets["root_ca"]))

    CONFIG["COMPOSE_REGISTRY_AUTH_DIR"] = str(docker_bind_source(auth_dir, CONFIG))
    CONFIG["COMPOSE_REGISTRY_CERTS_DIR"] = str(docker_bind_source(certs_dir, CONFIG))
    CONFIG["COMPOSE_REGISTRY_STORAGE_DIR"] = str(docker_bind_source(auth_dir / "storage", CONFIG))
    CONFIG["COMPOSE_REGISTRY_TLS_CERTIFICATE"] = "/auth/certs/domain.crt" if scheme == "https" else ""
    CONFIG["COMPOSE_REGISTRY_TLS_KEY"] = "/auth/certs/domain.key" if scheme == "https" else ""
    return {
        "auth_dir": str(auth_dir),
        "certs_dir": str(certs_dir),
        "htpasswd": str(htpasswd_path),
        "scheme": scheme,
        "endpoint": registry_endpoint(CONFIG),
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

    ca_line = ""
    if scheme == "https":
        ca_file = Path(config_str(CONFIG, "REGISTRY_CA_FILE", str(auth_dir / "certs" / "rootca.crt")))
        if not ca_file.is_file():
            die(f"Registry CA file not found: {ca_file}")
        shutil.copyfile(ca_file, registry_dir / "ca.crt")
        ca_line = f'ca = "/etc/containerd/certs.d/{hostport}/ca.crt"\n'

    hosts_toml = registry_dir / "hosts.toml"
    hosts_toml.write_text(
        "\n".join(
            [
                f'server = "{scheme}://{hostport}"',
                "",
                f'[host."{scheme}://{hostport}"]',
                'capabilities = ["pull", "resolve"]',
                ca_line.rstrip("\n"),
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


def configure_compose_port_binding() -> None:
    """Bind Compose-published ports on all interfaces inside the active Docker daemon."""
    CONFIG["COMPOSE_PORT_BIND_ADDR"] = "0.0.0.0"
    CONFIG["COMPOSE_PARALLEL_LIMIT"] = str(
        config_int(CONFIG, "DOCKER_BUILD_PARALLELISM", 4, minimum=1)
    )


def prepare_attacker_env_file() -> dict[str, object]:
    """Write dynamic attacker env vars that cannot be listed statically in Compose."""
    runtime_dir = Path(config_str(CONFIG, "RUNTIME_DIR", "/res/runtime"))
    attacker_dir = Path(config_str(CONFIG, "ATTACKER_RUNTIME_DIR", str(runtime_dir / "attacker")))
    if str(attacker_dir) == "/res/runtime/attacker":
        attacker_dir = runtime_dir / "attacker"
    attacker_dir.mkdir(parents=True, exist_ok=True)

    env_file = attacker_dir / "attacker.env"

    lines = []
    for name in sorted(CONFIG):
        if any(name.startswith(prefix) for prefix in ATTACKER_ENV_PREFIXES):
            lines.append(f"{name}={env_file_value(CONFIG[name])}")

    env_file.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

    CONFIG["COMPOSE_ATTACKER_ENV_FILE"] = str(env_file)
    CONFIG["ATTACKER_RUNTIME_DIR"] = str(attacker_dir)

    return {
        "path": str(env_file),
        "variables": [line.split("=", 1)[0] for line in lines],
    }


def main() -> None:
    code_root = Path(str(require_config(CONFIG, "CODE_ROOT")))
    created: dict[str, str] = {}

    for key, relative in DEFAULT_RELATIVE_DIRS.items():
        path = Path(str(set_config_default(CONFIG, key, str(code_root / relative))))
        path.mkdir(parents=True, exist_ok=True)
        created[key] = str(path)
        log(f"Ensured directory {key}={path}")

    runtime_dir = Path(config_str(CONFIG, "RUNTIME_DIR", "/res/runtime"))
    generated_dir = Path(str(set_config_default(CONFIG, "GENERATED_DIR", str(runtime_dir / "generated"))))
    generated_dir.mkdir(parents=True, exist_ok=True)

    created["GENERATED_DIR"] = str(generated_dir)
    log(f"Ensured directory GENERATED_DIR={generated_dir}")

    set_state_value(STATE, "prepared_directories", created)

    configure_compose_port_binding()

    # 03_compose.py now uses only config_to_env(CONFIG),
    # so values formerly added by compose_environment() must be prepared here.
    CONFIG["IMAGE_VERSION"] = image_version(CONFIG)

    set_state_value(STATE, "registry_assets", prepare_registry_assets())
    set_state_value(STATE, "kind_containerd_registry_config", prepare_kind_containerd_registry_config())
    set_state_value(STATE, "attacker_env_file", prepare_attacker_env_file())

    lab_name = config_str(CONFIG, "LAB_NAME", config_str(CONFIG, "CLUSTER_PROFILE", "honeypotlab"), allow_empty=False)
    CONFIG["COMPOSE_PROJECT_NAME"] = config_str(CONFIG, "COMPOSE_PROJECT_NAME", f"honeypot-{lab_name}")
    CONFIG["CP_NETWORK"] = config_str(CONFIG, "CP_NETWORK", f"kind-{lab_name}")

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

    if config_bool(CONFIG, "LOAD_GENERATOR_ENABLE", True):
        compose_services.append("load-generator")
        compose_build_services.append("load-generator")

    CONFIG["COMPOSE_SERVICES"] = compose_services
    CONFIG["COMPOSE_DEPLOY_SERVICES"] = compose_services
    CONFIG["COMPOSE_BUILD_SERVICES"] = compose_build_services

    caldera_root = Path(config_str(CONFIG, "CALDERA_ROOT", str(code_root / "caldera")))

    if config_bool(CONFIG, "CALDERA_SERVER_ENABLE", True):
        local_config = caldera_root / "local.yml"
        source_abilities_dir = caldera_root / "abilities"
        adversaries_dir = caldera_root / "adversaries"

        missing = [path for path in (local_config, source_abilities_dir, adversaries_dir) if not path.exists()]
        if missing:
            raise SystemExit("Missing Caldera assets: " + ", ".join(str(path) for path in missing))

        abilities_dir = prepare_caldera_abilities_mount(caldera_root, generated_dir)
        CONFIG["COMPOSE_CALDERA_LOCAL_YML"] = str(docker_bind_source(local_config, CONFIG))
        CONFIG["COMPOSE_CALDERA_ABILITIES_DIR"] = str(docker_bind_source(abilities_dir, CONFIG))
        CONFIG["COMPOSE_CALDERA_ADVERSARIES_DIR"] = str(docker_bind_source(adversaries_dir, CONFIG))

    set_skaffold_template_values()

    for key in ("KC0101", "KC0102", "KC0103", "KC0104", "KC0105", "KC0106", "KC0107", "KC0108"):
        CONFIG.setdefault(key, "")

    set_state_value(STATE, "compose_services", compose_services)
    set_state_value(STATE, "compose_build_services", compose_build_services)

    log("OpenTelemetry runtime preparation completed.")


if __name__ == "__main__":
    main()
