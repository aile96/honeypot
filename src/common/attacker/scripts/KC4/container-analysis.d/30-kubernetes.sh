#!/usr/bin/env bash
if [[ -d "${SA_DIR}" ]]; then
  ce_section "Kubernetes serviceaccount detected"
  ce_run "ls -la ${SA_DIR}" ls -la "${SA_DIR}"
  if [[ -f "${SA_DIR}/token" ]]; then
    echo "ServiceAccount token (first 200 chars, redacted) ->" | tee -a "${LOG}"
    head -c 200 "${SA_DIR}/token" | sed -E 's/(.{10}).*(.{10})/\1...REDACTED...\2/' | tee -a "${LOG}" || true
    ce_section "Kubernetes API quick checks"
    ce_api_get "/version" | head -c 1000 | tee -a "${LOG}" || true
    echo | tee -a "${LOG}"
    ce_api_get "/api/v1/namespaces" | jq -r '.items[].metadata.name' 2>/dev/null | tee -a "${LOG}" || true
    cp -a "${SA_DIR}/token" "${OUTDIR}/sa_token" || true
  fi
else
  echo "No Kubernetes serviceaccount dir at ${SA_DIR}" | tee -a "${LOG}"
fi

[[ -r /root/.kube/config ]] && cp -a /root/.kube/config "${OUTDIR}/root_kube_config" || true
