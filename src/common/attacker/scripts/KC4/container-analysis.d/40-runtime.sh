#!/usr/bin/env bash
ce_run "mount" mount
ce_run "df -h" df -h
ce_section "Container runtime sockets"
for socket_path in "${SOCKETS[@]}"; do
  if [[ -S "${socket_path}" ]]; then
    echo "FOUND_SOCKET: ${socket_path}" | tee -a "${LOG}"
    ls -l "${socket_path}" | tee -a "${LOG}" || true
  fi
done

ce_section "Process capabilities"
[[ -f /proc/self/status ]] && grep -E 'CapEff|CapPrm|CapBnd|CapInh' /proc/self/status | tee -a "${LOG}" || true
ce_run "sysctl limited" sh -c "sysctl -a 2>/dev/null | egrep -i 'net\\.|kernel\\.' | head -n 50"

ce_section "Common binaries"
for bin_name in "${BINARIES[@]}"; do
  if command -v "${bin_name}" >/dev/null 2>&1; then
    echo "FOUND: ${bin_name} -> $(command -v "${bin_name}")" | tee -a "${LOG}"
  else
    echo "MISSING: ${bin_name}" >> "${LOG}"
  fi
done
