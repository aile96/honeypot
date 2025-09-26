#!/usr/bin/env bash
set -eu

# --- Config ---
NS="${NS:-kube-system}"
CM_NAME="${CM_NAME:-coredns}"
APISERVER="${APISERVER:-https://${KUBERNETES_SERVICE_HOST}:${KUBERNETES_SERVICE_PORT}}"
TOKEN="${TOKEN:-$(cat /token)}"
CACERT="${CACERT:-/var/run/secrets/kubernetes.io/serviceaccount/ca.crt}"
REWRITE_LINE='rewrite name auth.app.svc.cluster.local image-provider.dmz.svc.cluster.local'
RESTART="${RESTART:-0}"    # metti a 1 per forzare restart di coredns

# --- HTTP helpers (solo risorse, niente "/") ---
api_get() {
  path="$1"
  curl --fail --silent --show-error \
    --cacert "$CACERT" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Accept: application/json" \
    "$APISERVER$path"
}

api_patch_json() {
  path="$1"
  curl --fail --silent --show-error \
    --cacert "$CACERT" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/merge-patch+json" \
    -X PATCH \
    --data-binary @- \
    "$APISERVER$path"
}

api_patch_strategic() {
  path="$1"
  curl --fail --silent --show-error \
    --cacert "$CACERT" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/strategic-merge-patch+json" \
    -X PATCH \
    --data-binary @- \
    "$APISERVER$path"
}

# --- 1) Leggi ConfigMap coredns ---
set +e
CM_JSON="$(api_get "/api/v1/namespaces/${NS}/configmaps/${CM_NAME}" 2>&1)"
rc="$?"
set -e

if [ "$rc" -ne 0 ]; then
  echo "$CM_JSON" | grep -qi "NotFound" && { echo "Errore: ConfigMap ${CM_NAME} non trovato in ${NS}." >&2; exit 1; }
  echo "$CM_JSON" | grep -qi "Forbidden" && { echo "403 Forbidden: SA corrente senza permessi su configmaps/${CM_NAME} in ${NS}." >&2; exit 1; }
  echo "$CM_JSON" >&2
  exit 1
fi

CORE_ORIG="$(printf '%s' "$CM_JSON" | jq -r '.data.Corefile')"
if [ -z "$CORE_ORIG" ] || [ "$CORE_ORIG" = "null" ]; then
  echo "Errore: .data.Corefile assente nel ConfigMap ${CM_NAME} in ${NS}" >&2
  exit 1
fi

# --- 2) Prepara nuovo Corefile (inserendo la rewrite FUORI da health, indentazione 4 spazi) ---
printf '%s\n' "$CORE_ORIG" > /tmp/Corefile.orig

# Se la rewrite è già al livello top del server block (depth==1), non servono modifiche
if awk -v ins="$REWRITE_LINE" '
  BEGIN{depth=0; found=0}
  {
    line=$0
    t=line; sub(/^[[:space:]]*/,"",t)
    if (depth==1 && t==ins) { found=1; exit }
    oc=gsub(/{/,"{"); cc=gsub(/}/,"}"); depth+=oc-cc
  }
  END{ exit found?0:1 }
' /tmp/Corefile.orig; then
  echo "[=] Rewrite già presente al livello corretto. Nessuna modifica."
  NEED_PATCH=0
else
  # Se esiste un blocco health a livello top, inserisci la rewrite SUBITO DOPO la chiusura di health
  if grep -qE '^[[:space:]]*health([[:space:]]|$)' /tmp/Corefile.orig; then
    awk -v ins="$REWRITE_LINE" '
      BEGIN{depth=0; in_health=0; inserted=0}
      {
        line=$0

        # Rimuovi rewrite eventualmente già presente ma NON al top-level
        t=line; sub(/^[[:space:]]*/,"",t)
        if (t==ins && depth!=1) next

        print line

        # Segna ingresso in health a livello top
        if (depth==1 && line ~ /^[[:space:]]*health([[:space:]]|$)/) in_health=1

        oc=gsub(/{/,"{"); cc=gsub(/}/,"}"); depth+=oc-cc

        # Uscita da health: tornati a depth==1 -> inserisci rewrite subito dopo il blocco
        if (in_health && depth==1 && !inserted) {
          printf "    %s\n", ins
          inserted=1
          in_health=0
        }
      }
    ' /tmp/Corefile.orig > /tmp/Corefile.new
  else
    # Altrimenti inserisci la rewrite subito dopo l’apertura del server block ".:53 {"
    awk -v ins="$REWRITE_LINE" '
      BEGIN{inserted=0}
      {
        line=$0
        # rimuovi qualsiasi riga identica alla rewrite per evitare duplicati
        t=line; sub(/^[[:space:]]*/,"",t)
        if (t==ins) next

        print line
        if (!inserted && line ~ /^[[:space:]]*\.\:53[[:space:]]*\{[[:space:]]*$/) {
          printf "    %s\n", ins
          inserted=1
        }
      }
    ' /tmp/Corefile.orig > /tmp/Corefile.new
  fi
  NEED_PATCH=1
fi

# --- 3) Applica patch al ConfigMap (solo se necessario) ---
if [ "${NEED_PATCH}" -eq 1 ]; then
  echo "[+] Patch del ConfigMap ${CM_NAME} in ${NS}..."
  CORE_NEW="$(cat /tmp/Corefile.new)"
  jq -n --arg core "$CORE_NEW" '{data:{Corefile:$core}}' \
    | api_patch_json "/api/v1/namespaces/${NS}/configmaps/${CM_NAME}" >/dev/null
  echo "[+] Patch applicata."
else
  echo "[=] Nessuna patch applicata."
fi

# --- 4) Restart CoreDNS opzionale (annotation sul Pod template) ---
if [ "$RESTART" = "1" ]; then
  ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  DEP_PATH="/apis/apps/v1/namespaces/${NS}/deployments/coredns"
  echo "[*] Richiesta di restart CoreDNS (annotation coredns/reloadedAt=$ts)..."
  jq -n --arg t "$ts" '{spec:{template:{metadata:{annotations:{"coredns/reloadedAt":$t}}}}}' \
    | api_patch_strategic "$DEP_PATH" >/dev/null || {
      echo "[!] Patch Deployment fallita (403/404 o nome diverso?). Controlla permessi e nome." >&2
      exit 1
    }
  echo "[*] Restart richiesto."
fi

echo "[OK] Done."
