# Dockerfile: router tra reti Docker (kind <-> bridge/web)
FROM alpine:3.20

# iptables + strumenti di rete
RUN apk add --no-cache iptables iproute2 bash

# EntryPoint senza sysctl: solo regole iptables/NAT
RUN printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  '' \
  '# Interfacce (ordine di attach: eth0=rete del run, eth1=rete connessa dopo)' \
  'IF0=${IF0:-eth0}' \
  'IF1=${IF1:-eth1}' \
  '' \
  '# Pulizia idempotente' \
  'iptables -F || true' \
  'iptables -t nat -F || true' \
  'iptables -t mangle -F || true' \
  'iptables -X || true' \
  '' \
  '# Consenti forwarding e NAT bidirezionale' \
  'iptables -P FORWARD ACCEPT' \
  '# NAT/MASQUERADE su entrambe le uscite' \
  'iptables -t nat -A POSTROUTING -o "$IF0" -j MASQUERADE' \
  'iptables -t nat -A POSTROUTING -o "$IF1" -j MASQUERADE' \
  '' \
  '# Traffico di ritorno' \
  'iptables -A FORWARD -i "$IF0" -o "$IF1" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT' \
  'iptables -A FORWARD -i "$IF1" -o "$IF0" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT' \
  '' \
  '# Traffico diretto' \
  'iptables -A FORWARD -i "$IF0" -o "$IF1" -j ACCEPT' \
  'iptables -A FORWARD -i "$IF1" -o "$IF0" -j ACCEPT' \
  '' \
  'echo "Router pronto tra $IF0 <-> $IF1"' \
  'echo "--- iptables -S ---"; iptables -S || true' \
  'echo "--- iptables -t nat -S ---"; iptables -t nat -S || true' \
  '' \
  '# Mantieni vivo il container' \
  'exec tail -f /dev/null' \
  > /usr/local/bin/entrypoint.sh && chmod +x /usr/local/bin/entrypoint.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
