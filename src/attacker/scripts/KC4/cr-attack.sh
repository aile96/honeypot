#!/usr/bin/env bash
set -euo pipefail

cat >/etc/crictl.yaml <<'YAML'
runtime-endpoint: unix:///host/run/containerd/containerd.sock
image-endpoint: unix:///host/run/containerd/containerd.sock
timeout: 10
debug: false
YAML

crictl exec -i \
  $(crictl ps | awk '$7 == "traffic-controller" {print $1}') \
  cat /host/proc/$(crictl inspect -o go-template --template '{{.info.pid}}' "$(crictl ps -q --name cilium-operator)")/root/var/run/secrets/kubernetes.io/serviceaccount/token > /tmp/token
