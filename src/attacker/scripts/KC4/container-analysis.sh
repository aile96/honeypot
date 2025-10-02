#!/usr/bin/env bash
# Scopo: enumerazione completa (INFO-ONLY) di un container.

set -euo pipefail
TS=$(date +%Y%m%d_%H%M%S)
OUTDIR="/tmp/exfiltration/container-enum-${TS}"
mkdir -p "${OUTDIR}"
LOG="${OUTDIR}/report.txt"
JSON="${OUTDIR}/report.json"
DEBIAN_FRONTEND=noninteractive apt-get -qq update >/dev/null 2>&1 && apt-get -qq install -y iproute2 psmisc jq iptables cron >/dev/null 2>&1

echo "Container enumeration report" > "${LOG}"
echo "Timestamp: $(date -u +"%Y-%m-%dT%H:%M:%SZ")" >> "${LOG}"
echo "Output directory: ${OUTDIR}" >> "${LOG}"
echo "----" >> "${LOG}"

# helper: safe command run - write header and output
run() {
  echo -e "\n== $1 ==" | tee -a "${LOG}"
  echo "### $1" >> "${OUTDIR}/raw.txt"
  shift
  if command -v "$1" >/dev/null 2>&1 || [ -x "$1" ] 2>/dev/null; then
    "$@" 2>&1 | tee -a "${LOG}"
  else
    echo "COMMAND_MISSING: $1" | tee -a "${LOG}"
  fi
}

# Basic system & identity
echo "### Basic system & identity" >> "${LOG}"
run "whoami" whoami
run "id" id
run "hostname" hostname
run "uname -a" uname -a || true
run "cat /etc/os-release" cat /etc/os-release || true
run "uptime" uptime || true

# Process & scheduling
run "ps aux (first 200 lines)" ps aux | head -n 200 || true
run "pstree (if present)" pstree -p || echo "pstree not present"

# Shells and users
run "getent passwd (users)" getent passwd || cat /etc/passwd || true
run "getent group" getent group || cat /etc/group || true

# Environment variables (mask potential secrets heuristically)
echo -e "\n== Environment (filtered) ==" | tee -a "${LOG}"
env | sed -E 's/(TOKEN|PASSWORD|PASS|SECRET|KEY|CRED)[^=]*=.*/\1=REDACTED/Ig' | tee -a "${LOG}" >> "${OUTDIR}/env_redacted.txt"

# Network enumeration
run "ip addr" ip addr || /sbin/ip addr || true
run "ip route" ip route || /sbin/ip route || true
run "ss -tunlp (socket list)" ss -tunlp || netstat -tunlp || true
run "iptables -L" iptables -L -n || echo "iptables missing or no permission"

# Mounted filesystems and mounts
run "mount" mount || true
run "df -h" df -h || true
run "find / -maxdepth 2 -type d -name '*secret*' 2>/dev/null | head -n 50" find / -maxdepth 2 -type d -name '*secret*' 2>/dev/null | head -n 50 || true

# Look for Kubernetes serviceaccount token mounts
SA_DIR="/var/run/secrets/kubernetes.io/serviceaccount"
if [ -d "${SA_DIR}" ]; then
  echo -e "\n== Kubernetes serviceaccount detected ==" | tee -a "${LOG}"
  run "ls -la ${SA_DIR}" ls -la "${SA_DIR}" || true
  if [ -f "${SA_DIR}/token" ]; then
    echo "ServiceAccount token (first 200 chars, redacted) ->" | tee -a "${LOG}"
    head -c 200 "${SA_DIR}/token" | sed -E 's/(.{10}).*(.{10})/\1...REDACTED...\2/' | tee -a "${LOG}"
    # Attempt to curl API (insecure flag used to avoid CA issues). Only show minimal info.
    if command -v curl >/dev/null 2>&1 ; then
      API="https://kubernetes.default.svc"
      TOKEN=$(cat ${SA_DIR}/token)
      echo -e "\n== Kubernetes API quick checks ==" | tee -a "${LOG}"
      echo "Attempting: GET ${API}/version (using token, insecure TLS)" | tee -a "${LOG}"
      curl --max-time 10 -sSk -H "Authorization: Bearer ${TOKEN}" "${API}/version" | head -c 1000 | tee -a "${LOG}" || echo "curl failed or no network"
      echo -e "\nListing namespaces names (limited view)..." | tee -a "${LOG}"
      curl --max-time 10 -sSk -H "Authorization: Bearer ${TOKEN}" "${API}/api/v1/namespaces" | jq -r '.items[].metadata.name' 2>/dev/null | tee -a "${LOG}" || echo "jq missing or API inaccessible"
    else
      echo "curl not found; skipping API probes" | tee -a "${LOG}"
    fi
  fi
else
  echo "No Kubernetes serviceaccount dir at ${SA_DIR}" | tee -a "${LOG}"
fi

# Docker socket / container runtime sockets
SOCKETS=(/var/run/docker.sock /run/containerd/containerd.sock /var/run/crio/crio.sock)
for s in "${SOCKETS[@]}"; do
  if [ -S "${s}" ]; then
    echo -e "\n== Found socket: ${s} ==" | tee -a "${LOG}"
    ls -l "${s}" | tee -a "${LOG}"
  fi
done

# Check capabilities and privileged flags
echo -e "\n== Process capabilities and kernel info ==" | tee -a "${LOG}"
if [ -f /proc/self/status ]; then
  grep -E 'CapEff|CapPrm|CapBnd|CapInh' /proc/self/status | tee -a "${LOG}" || true
fi
run "sysctl -a (limited to container-safe patterns)" sysctl -a 2>/dev/null | egrep -i 'net\.|kernel\.' | head -n 50 || true

# Check for writable sensitive paths and hostPath mounts
CRITICAL_PATHS=("/" "/root" "/host" "/hostfs" "/etc/kubernetes" "/var/run/docker.sock")
for p in "${CRITICAL_PATHS[@]}"; do
  if [ -e "${p}" ]; then
    echo "${p} exists" | tee -a "${LOG}"
    ls -la "${p}" 2>/dev/null | head -n 20 >> "${LOG}" || true
  fi
done

# Installed binaries and common tooling (checks presence only)
BINARIES=(kubectl oc docker crictl curl wget nc ncat netcat ss jq python3 python perl busybox stat)
echo -e "\n== Checking common binaries ==" | tee -a "${LOG}"
for b in "${BINARIES[@]}"; do
  if command -v "${b}" >/dev/null 2>&1; then
    echo "FOUND: ${b} -> $(command -v ${b})" | tee -a "${LOG}"
  else
    echo "MISSING: ${b}" >> "${LOG}"
  fi
done

# Search for known file patterns that may contain secrets (heuristic, read-only)
echo -e "\n== Heuristic search for files that may contain secrets (first 200 results) ==" | tee -a "${LOG}"
grep -R --binary-files=without-match -I -nE "passwd|password|secret|api_key|apiKey|PRIVATE_KEY|BEGIN RSA PRIVATE KEY|BEGIN OPENSSH PRIVATE KEY" / 2>/dev/null | head -n 200 | tee -a "${LOG}" || true

# TLS certs and known keys (list)
echo -e "\n== Listing /etc/ssl /etc/ssh and private key candidates ==" | tee -a "${LOG}"
ls -la /etc/ssh 2>/dev/null | tee -a "${LOG}" || true
find /etc -type f -iname "*key*" -o -iname "*.pem" -maxdepth 3 2>/dev/null | head -n 100 | tee -a "${LOG}" || true

# Cronjobs, systemd not usually in containers but list relevant dirs
run "crontab -l (if available)" crontab -l || true
run "ls /etc/cron* (if present)" ls -la /etc/cron* 2>/dev/null || true

# File capability checks (look for setuid files)
echo -e "\n== setuid / setgid files (top 100) ==" | tee -a "${LOG}"
find / -perm /6000 -type f -exec ls -ld {} \; 2>/dev/null | head -n 100 | tee -a "${LOG}" || true

# Kernel vulnerabilities quick info (non-exploit): report kernel version
run "uname -r (kernel version)" uname -r || true

# Network neighbors and DNS
run "cat /etc/resolv.conf" cat /etc/resolv.conf || true
run "ip neigh" ip neigh || true

# Save copies of files that are commonly sensitive (if readable) - token and kube config
if [ -r "/root/.kube/config" ]; then
  cp -a /root/.kube/config "${OUTDIR}/root_kube_config" || true
  echo "Copied root kubeconfig to ${OUTDIR}/root_kube_config" | tee -a "${LOG}"
fi
if [ -r "${SA_DIR}/token" ]; then
  cat "${SA_DIR}/token" > "${OUTDIR}/sa_token" || true
  echo "Copied serviceaccount token to ${OUTDIR}/sa_token (handle securely)" | tee -a "${LOG}"
fi

# Summarize findings into a small JSON (best-effort)
cat > "${JSON}" <<EOF
{
  "timestamp": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")",
  "hostname": "$(hostname 2>/dev/null || echo unknown)",
  "user": "$(id -un 2>/dev/null || echo unknown)",
  "uid": "$(id -u 2>/dev/null || echo unknown)",
  "has_k8s_serviceaccount": "$( [ -d "${SA_DIR}" ] && echo true || echo false )",
  "sockets_found": "$(for s in "${SOCKETS[@]}"; do [ -S "$s" ] && echo "$s " || true; done)",
  "binaries_found": "$(for b in ${BINARIES[*]}; do command -v $b >/dev/null 2>&1 && echo $b; done | tr '\n' ',' )"
}
EOF

echo -e "\n----\nEnumeration finished. Output saved under ${OUTDIR}" | tee -a "${LOG}"
