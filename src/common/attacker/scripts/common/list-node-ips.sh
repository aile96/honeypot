#!/usr/bin/env bash

# Read "IP - HOST" entries and return worker node IPs.
# Usage: list_worker_node_ips [iphost_file]
list_worker_node_ips() {
  local file_ip="${1:-/tmp/iphost}"

  if [[ ! -f "${file_ip}" ]]; then
    echo "Error: file '${file_ip}' not found" >&2
    return 1
  fi

  echo ">> Recover IPs list (file: ${file_ip})..." >&2
  awk -F'-' '
    /^[[:space:]]*#/ { next }
    /^[[:space:]]*$/ { next }
    NF >= 2 {
      ip=$1; host=$2
      gsub(/^[ \t]+|[ \t\r]+$/, "", ip)
      gsub(/^[ \t]+|[ \t\r]+$/, "", host)
      if (host ~ /^worker([0-9]+)?$/ && ip ~ /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/) {
        print ip
      }
    }
  ' "${file_ip}" | sort -u
}
