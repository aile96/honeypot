#!/usr/bin/env bash
ce_section "Basic system and identity"
ce_run "whoami" whoami
ce_run "id" id
ce_run "hostname" hostname
ce_run "uname -a" uname -a
ce_run "cat /etc/os-release" cat /etc/os-release
ce_run "uptime" uptime
ce_run "ps aux" ps aux
ce_run "getent passwd" getent passwd
ce_run "getent group" getent group

ce_section "Environment (filtered)"
env | sed -E 's/(TOKEN|PASSWORD|PASS|SECRET|KEY|CRED)[^=]*=.*/\1=REDACTED/Ig' | tee -a "${LOG}" > "${OUTDIR}/env_redacted.txt"
