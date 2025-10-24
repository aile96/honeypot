#!/usr/bin/env bash
set -euo pipefail

# Install dependencies needed by the project.
# Supports: Ubuntu/Debian, RHEL/CentOS/Fedora, Arch, macOS (Homebrew).
# Installs/ensures: docker, docker compose v2, kind, kubectl, helm, htpasswd, openssl, skaffold, jq, curl, sed, awk, grep

### === Funzioni di utilità ===
log() { printf "\033[1;36m[INFO]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[WARN]\033[0m %s\n" "$*"; }
err() { printf "\033[1;31m[ERR ]\033[0m %s\n" "$*" >&2; }
die() { echo -e "[ERROR] $*" >&2; exit 1; }
need_cmd(){ command -v "$1" >/dev/null 2>&1; }

declare -A HELM_REPOS=(
  ["open-telemetry"]="https://open-telemetry.github.io/opentelemetry-helm-charts"
  ["jaegertracing"]="https://jaegertracing.github.io/helm-charts"
  ["prometheus-community"]="https://prometheus-community.github.io/helm-charts"
  ["grafana"]="https://grafana.github.io/helm-charts"
  ["opensearch"]="https://opensearch-project.github.io/helm-charts"
  ["cilium"]="https://helm.cilium.io/"
  ["metallb"]="https://metallb.github.io/metallb"
  ["csi-driver-smb"]="https://raw.githubusercontent.com/kubernetes-csi/csi-driver-smb/master/charts"
)

detect_platform() {
  OS="$(uname -s)"
  case "$OS" in
    Linux)
      if [ -f /etc/os-release ]; then
        . /etc/os-release
        ID_LIKE="${ID_LIKE:-}"
        case "${ID:-unknown}:${ID_LIKE:-}" in
          debian:*|ubuntu:*|linuxmint:*|*:*debian*|*:*ubuntu*) PM="apt";;
          fedora:*|rhel:*|centos:*|rocky:*|almalinux:*|*:*rhel*|*:*fedora*) PM="dnf"; command -v dnf >/dev/null 2>&1 || PM="yum";;
          arch:*|*:*arch*) PM="pacman";;
          *) PM="unknown";;
        esac
      else
        PM="unknown"
      fi
      PLATFORM="linux"
      ;;
    Darwin)
      PLATFORM="darwin"
      PM="brew"
      ;;
    *)
      err "OS non supportato: $OS"
      exit 1
      ;;
  esac
  echo "$PLATFORM:$PM"
}

ensure_sudo() {
  if [ "$(id -u)" -ne 0 ]; then
    if need_cmd sudo; then
      SUDO="sudo"
    else
      warn "sudo non trovato. Provo senza privilegi — alcune installazioni potrebbero fallire."
      SUDO=""
    fi
  else
    SUDO=""
  fi
}

install_pkg_linux() {
  local pkgs=("$@")
  case "$PM" in
    apt)
      $SUDO apt-get update -y
      $SUDO apt-get install -y --no-install-recommends "${pkgs[@]}"
      ;;
    dnf|yum)
      $SUDO $PM install -y "${pkgs[@]}"
      ;;
    pacman)
      $SUDO pacman -Sy --noconfirm "${pkgs[@]}"
      ;;
    *)
      err "Package manager Linux non supportato: $PM"
      exit 1
      ;;
  esac
}

ensure_docker() {
  if need_cmd docker; then log "docker già presente."; return; fi
  log "Installo docker…"
  case "$PM" in
    apt) install_pkg_linux docker.io ;;
    dnf|yum) install_pkg_linux docker ;;
    pacman) install_pkg_linux docker ;;
    *) err "Impossibile installare docker con PM=$PM"; exit 1;;
  esac
  if need_cmd systemctl; then
    $SUDO systemctl enable --now docker || true
  fi
  if getent group docker >/dev/null 2>&1; then
    if ! id -nG "$USER" | grep -q "\bdocker\b"; then
      $SUDO usermod -aG docker "$USER" || true
      warn "Aggiunto $USER al gruppo docker. Effettua logout/login per applicare."
    fi
  fi
  newgrp docker
}

ensure_docker_compose() {
  # Verifica se 'docker compose' (plugin v2) funziona
  if docker compose version >/dev/null 2>&1; then
    log "docker compose v2 (plugin) già presente."
    return
  fi

  # Verifica se 'docker-compose' (standalone) funziona
  if docker-compose version >/dev/null 2>&1; then
    log "docker-compose standalone già presente."
    return
  fi

  # Se non c'è nulla, scarica il binario standalone
  log "Installo Docker Compose standalone (ultima versione trovata su GitHub)…"

  sudo apt install docker-compose -y

  # Verifica post-install
  if docker-compose version >/dev/null 2>&1; then
    log "Docker Compose standalone installato con successo."
  else
    err "Docker Compose non disponibile. Installa manualmente 'docker-compose-plugin' o verifica Docker Desktop."
    exit 1
  fi
}

ensure_docker_buildx() {
  if docker buildx version >/dev/null 2>&1; then
    log "docker buildx già presente."
    return
  fi
  ARCH=$(uname -m)
  case "$ARCH" in
    x86_64) ARCH_TAG="amd64" ;;
    aarch64|arm64) ARCH_TAG="arm64" ;;
    armv7l) ARCH_TAG="armv7" ;;
    *) echo "Arch $ARCH non riconosciuta; esegui 'uname -m' e scarica manualmente"; exit 1 ;;
  esac

  # prendi il tag dell'ultima release (es. v0.29.0)
  TAG=$(curl -s https://api.github.com/repos/docker/buildx/releases/latest | grep -Po '"tag_name": "\K.*?(?=")')
  echo "Latest buildx tag: $TAG"

  # costruisci URL dell'asset e scarica (fallisce se asset non esiste)
  URL="https://github.com/docker/buildx/releases/download/${TAG}/buildx-${TAG}.linux-${ARCH_TAG}"
  echo "URL: $URL"

  mkdir -p ~/.docker/cli-plugins
  curl -fL "$URL" -o ~/.docker/cli-plugins/docker-buildx || {
    echo "Download fallito — prova pacchetto di sistema o controlla URL (output di curl qui sotto):"; curl -sI "$URL"; exit 1;
  }

  chmod +x ~/.docker/cli-plugins/docker-buildx
}

install_kind() {
  if need_cmd kind; then log "kind già presente."; return; fi
  log "Installo kind…"
  ARCH="$(uname -m)"
  case "$ARCH" in
    x86_64|amd64) ARCH="amd64";;
    aarch64|arm64) ARCH="arm64";;
    *) warn "Architettura non riconosciuta ($ARCH), provo amd64"; ARCH="amd64";;
  esac
  curl -fsSL "https://github.com/kubernetes-sigs/kind/releases/download/v0.23.0/kind-${PLATFORM}-${ARCH}" -o /tmp/kind
  chmod +x /tmp/kind
  $SUDO mv /tmp/kind /usr/local/bin/kind
}

install_kubectl() {
  if need_cmd kubectl; then log "kubectl già presente."; return; fi
  log "Installo kubectl…"
  if [ "$PLATFORM" = "darwin" ] && [ "$PM" = "brew" ]; then
    brew install kubectl
  else
    VER="$(curl -fsSL https://dl.k8s.io/release/stable.txt)"
    ARCH="$(uname -m)"
    case "$ARCH" in
      x86_64|amd64) ARCH="amd64";;
      aarch64|arm64) ARCH="arm64";;
      *) warn "Arch non riconosciuta ($ARCH), uso amd64"; ARCH="amd64";;
    esac
    curl -fsSLo /tmp/kubectl "https://dl.k8s.io/release/${VER}/bin/${PLATFORM}/${ARCH}/kubectl"
    chmod +x /tmp/kubectl
    $SUDO mv /tmp/kubectl /usr/local/bin/kubectl
  fi
}

install_helm() {
  if need_cmd helm; then log "helm già presente."; return; fi
  log "Installo helm…"
  if [ "$PLATFORM" = "darwin" ] && [ "$PM" = "brew" ]; then
    brew install helm
  else
    curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
  fi
}

ensure_htpasswd() {
  if need_cmd htpasswd; then log "htpasswd già presente."; return; fi
  log "Installo htpasswd…"
  case "$PM" in
    apt) install_pkg_linux apache2-utils ;;
    dnf|yum) install_pkg_linux httpd-tools ;;
    pacman) install_pkg_linux apache ;;
    brew) brew install httpd ;;
    *) err "Impossibile installare htpasswd (PM=$PM)"; exit 1;;
  esac
}

install_skaffold() {
  if need_cmd skaffold; then log "skaffold già presente."; return; fi
  log "Installo skaffold…"
  ARCH="$(uname -m)"
  OSv="$(uname | tr '[:upper:]' '[:lower:]')"
  case "$ARCH" in
    x86_64|amd64) ARCH="amd64";;
    aarch64|arm64) ARCH="arm64";;
    *) warn "Arch non riconosciuta ($ARCH), uso amd64"; ARCH="amd64";;
  esac
  if [ "$PLATFORM" = "darwin" ] && [ "$PM" = "brew" ]; then
    brew install skaffold
  else
    curl -fsSLo /tmp/skaffold "https://storage.googleapis.com/skaffold/releases/latest/skaffold-${OSv}-${ARCH}"
    chmod +x /tmp/skaffold
    $SUDO mv /tmp/skaffold /usr/local/bin/skaffold
  fi
}

ensure_basic_tools() {
  case "$PM" in
    apt) install_pkg_linux curl ca-certificates git jq openssl sed gawk grep ipcalc ;;
    dnf|yum) install_pkg_linux curl ca-certificates git jq openssl sed gawk grep ipcalc ;;
    pacman) install_pkg_linux curl ca-certificates git jq openssl sed gawk grep ipcalc ;;
    brew) brew install curl git jq openssl gawk grep ipcalc || true ;;
  esac
}

main() {
  IFS=":" read -r PLATFORM PM <<<"$(detect_platform)"
  log "Piattaforma: ${PLATFORM}, Package manager: ${PM}"
  ensure_sudo
  ensure_basic_tools

  if [ "$PLATFORM" = "darwin" ]; then
    if ! need_cmd brew; then
      err "Homebrew non trovato su macOS. Installalo da https://brew.sh/"
      exit 1
    fi
  fi

  ensure_docker
  ensure_docker_compose
  ensure_docker_buildx
  install_kind
  install_kubectl
  install_helm
  ensure_htpasswd
  install_skaffold

  log "Controllo versioni:"
  for c in docker docker-compose kind kubectl helm htpasswd openssl skaffold jq; do
    if [ "$c" = "docker compose" ]; then
      if docker compose version >/dev/null 2>&1; then
        printf " - docker compose: %s\n" "$(docker compose version | head -n1)"
      else
        printf " - docker compose: MANCANTE\n"
      fi
    else
      if need_cmd "${c%% *}"; then
        printf " - %s: %s\n" "$c" "$(${c%% *} --version 2>/dev/null | head -n1 || echo ok)"
      else
        printf " - %s: MANCANTE\n" "$c"
      fi
    fi
  done

  if [ "$PLATFORM" = "darwin" ]; then
    warn "Su macOS può servirti Docker Desktop: 'brew install --cask docker' e avvialo manualmente."
  fi

  log "Aggiunta dei repo Helm necessari al progetto"
  for repo in "${!HELM_REPOS[@]}"; do
    if helm repo list | grep -q "$repo"; then
      warn "Helm repo \"$repo\" già presente."
    else
      log "Aggiunta repo Helm: $repo"
      helm repo add "$repo" "${HELM_REPOS[$repo]}"
    fi
  done

  log "Aggiornamento dei repo Helm..."
  helm repo update

  log "Installazione dipendenze completata"
}

main "$@"
