#!/bin/bash

set -e

# ========================
# 🧰 Tools richiesti
# ========================
REQUIRED_TOOLS=("kind" "docker" "htpasswd" "openssl" "helm" "skaffold")

# ========================
# 🧭 Rileva il sistema operativo
# ========================
OS=""
if [[ "$OSTYPE" == "linux-gnu"* ]]; then
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        OS=$ID  # e.g. ubuntu, debian, centos
    fi
elif [[ "$OSTYPE" == "darwin"* ]]; then
    OS="macos"
else
    echo "❌ Sistema operativo non supportato: $OSTYPE"
    exit 1
fi

# ========================
# 🔧 Funzioni installazione per OS
# ========================

install_tool() {
  TOOL=$1
  echo "➡️  Installazione di $TOOL..."

  case $TOOL in
    kind)
      if [ "$OS" == "macos" ]; then
        brew install kind
      elif [[ "$OS" == "ubuntu" || "$OS" == "debian" ]]; then
        curl -Lo ./kind https://kind.sigs.k8s.io/dl/v0.22.0/kind-linux-amd64
        chmod +x ./kind
        sudo mv ./kind /usr/local/bin/kind
      else
        echo "⚠️  Installazione automatica non supportata per $TOOL su $OS"
      fi
      ;;
    
    docker)
      echo "⚠️  Installa Docker manualmente da: https://docs.docker.com/get-docker/"
      ;;

    htpasswd)
      if [ "$OS" == "macos" ]; then
        brew install httpd
      elif [[ "$OS" == "ubuntu" || "$OS" == "debian" ]]; then
        sudo apt-get update && sudo apt-get install -y apache2-utils
      elif [[ "$OS" == "centos" || "$OS" == "rhel" ]]; then
        sudo yum install -y httpd-tools
      fi
      ;;

    openssl)
      if [ "$OS" == "macos" ]; then
        brew install openssl
      elif [[ "$OS" == "ubuntu" || "$OS" == "debian" ]]; then
        sudo apt-get install -y openssl
      fi
      ;;

    helm)
      if [ "$OS" == "macos" ]; then
        brew install helm
      elif [[ "$OS" == "ubuntu" || "$OS" == "debian" ]]; then
        curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
      fi
      ;;

    skaffold)
      if [ "$OS" == "macos" ]; then
        brew install skaffold
      elif [[ "$OS" == "ubuntu" || "$OS" == "debian" ]]; then
        curl -Lo skaffold https://storage.googleapis.com/skaffold/releases/latest/skaffold-linux-amd64
        chmod +x skaffold
        sudo mv skaffold /usr/local/bin/
      fi
      ;;

    *)
      echo "❌ Tool non gestito: $TOOL"
      ;;
  esac
}

# ========================
# 🚀 Controllo e installazione
# ========================
echo "🔍 Controllo tools richiesti..."

for tool in "${REQUIRED_TOOLS[@]}"; do
  if ! command -v "$tool" &>/dev/null; then
    echo "❌ Tool mancante: $tool"
    install_tool "$tool"
  else
    echo "✅ $tool è già installato."
  fi
done

#if ! command -v flux &>/dev/null; then
#  echo "➡️  Installazione CLI Flux..."
#  curl -s https://fluxcd.io/install.sh | sudo bash
#else
#  echo "✅ CLI Flux già installata."
#fi

echo "✅ Tutti i tool sono presenti o sono stati installati."
