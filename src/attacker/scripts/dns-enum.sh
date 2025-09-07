#!/bin/sh
set -eu

OUT_FILE="${1:-/tmp/dns-enum-results.csv}"

DNS_IP="$(awk '/^nameserver/{print $2; exit}' /etc/resolv.conf)"
CLUSTER_DOMAIN="$(awk '/^search/ {for(i=2;i<=NF;i++) if ($i ~ /\.svc\./){sub(/.*\.svc\./,"",$i); print $i}}' /etc/resolv.conf | head -n1)"
CUR_NS="$(cat /var/run/secrets/kubernetes.io/serviceaccount/namespace 2>/dev/null || echo default)"

# Wordlist servizi/namespace (sovrascrivibili via env)
WORDLIST="${WORDLIST:-kubernetes kube-dns coredns api apiserver etcd prometheus grafana loki tempo jaeger zipkin elasticsearch kibana \
logstash fluentd fluent-bit rabbitmq kafka zookeeper nats redis mysql mariadb postgres postgresql mongo mongodb minio registry harbor \
jenkins gitlab argocd tekton keycloak vault consul ingress-nginx traefik istiod istio-pilot istio-ingress webhook dashboard metrics-server \
kube-state-metrics opensearch valkey-cart}"
COMMON_NS="${EXTRA_NAMESPACES:-default kube-system kube-public monitoring logging observability dev data test qa stage staging prod production \
ingress-nginx cert-manager mem dat}"

# build lista namespace (dedup includendo quello corrente)
NS_LIST=$(printf '%s\n' "$CUR_NS" $COMMON_NS | awk 'NF && !x[$0]++')

echo "name,ip" > "$OUT_FILE"

# probe base (kubernetes.default)
if [ -n "${CLUSTER_DOMAIN:-}" ]; then
  for ip in $(dig +short +time=1 +tries=1 @"$DNS_IP" "kubernetes.default.svc.${CLUSTER_DOMAIN}" A AAAA 2>/dev/null); do
    echo "kubernetes.default.svc.${CLUSTER_DOMAIN},$ip" >> "$OUT_FILE"
  done
fi

# brute-force servizi comuni per namespace comuni
for ns in $NS_LIST; do
  for svc in $WORDLIST; do
    if [ -n "${CLUSTER_DOMAIN:-}" ]; then
      FQDN="${svc}.${ns}.svc.${CLUSTER_DOMAIN}"
    else
      FQDN="${svc}.${ns}.svc"
    fi
    for t in A AAAA; do
      for ip in $(dig +short +time=1 +tries=1 @"$DNS_IP" "$FQDN" $t 2>/dev/null); do
        [ -n "$ip" ] && echo "$FQDN,$ip" >> "$OUT_FILE"
      done
    done
  done
done

# dedup mantenendo header
tmp="${OUT_FILE}.tmp"
head -n1 "$OUT_FILE" > "$tmp"
tail -n +2 "$OUT_FILE" | sort -u >> "$tmp" || true
mv "$tmp" "$OUT_FILE"

[ "$(wc -l < "$OUT_FILE")" -gt 1 ] && echo "[+] Results -> $OUT_FILE" || echo "[-] No DNS hits"