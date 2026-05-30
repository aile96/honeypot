#!/usr/bin/env bash
cat > "${JSON}" <<EOF
{
  "timestamp": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")",
  "hostname": "$(hostname 2>/dev/null || echo unknown)",
  "user": "$(id -un 2>/dev/null || echo unknown)",
  "uid": "$(id -u 2>/dev/null || echo unknown)",
  "has_k8s_serviceaccount": "$( [[ -d "${SA_DIR}" ]] && echo true || echo false )",
  "sockets_found": "$(for s in "${SOCKETS[@]}"; do [[ -S "$s" ]] && printf '%s ' "$s" || true; done)",
  "binaries_found": "$(for b in "${BINARIES[@]}"; do command -v "$b" >/dev/null 2>&1 && echo "$b"; done | tr '\n' ',')"
}
EOF

echo -e "\n----\nEnumeration finished. Output saved under ${OUTDIR}" | tee -a "${LOG}"
