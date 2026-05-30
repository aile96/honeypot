#!/usr/bin/env bash
ce_section "Critical paths"
for path_name in / /root /host /hostfs /etc/kubernetes /var/run/docker.sock; do
  if [[ -e "${path_name}" ]]; then
    echo "${path_name} exists" | tee -a "${LOG}"
    ls -la "${path_name}" 2>/dev/null | head -n 20 >> "${LOG}" || true
  fi
done

ce_section "Secret-like files and content"
find / -maxdepth 2 -type d -name '*secret*' 2>/dev/null | head -n 50 | tee -a "${LOG}" || true
grep -R --binary-files=without-match -I --devices=skip \
  --exclude-dir={proc,sys,dev,tmp,run,procfs,var/lib/docker,host/proc,host/sys,host/dev,host/tmp,host/run,host/procfs,host/var/lib/docker} \
  -nE "passwd|password|secret|api_key|apiKey|PRIVATE_KEY|BEGIN RSA PRIVATE KEY|BEGIN OPENSSH PRIVATE KEY" / 2>/dev/null \
  | head -n 200 | tee -a "${LOG}" || true

ce_section "Certificates and private key candidates"
ls -la /etc/ssh 2>/dev/null | tee -a "${LOG}" || true
find /etc -maxdepth 3 -type f \( -iname "*key*" -o -iname "*.pem" \) 2>/dev/null | head -n 100 | tee -a "${LOG}" || true

ce_section "setuid/setgid files"
find / -perm /6000 -type f -exec ls -ld {} \; 2>/dev/null | head -n 100 | tee -a "${LOG}" || true
