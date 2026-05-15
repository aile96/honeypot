#!/usr/bin/env bash
set -euo pipefail
KUBECONFIG="${KUBECONFIG:-$DATA_PATH/KC6/ops-admin.kubeconfig}"
OUTFILE="${OUTFILE:-$DATA_PATH/KC6/secrets}"
mkdir -p "$(dirname -- "$OUTFILE")"

kubectl --kubeconfig "$KUBECONFIG" get secrets --all-namespaces -o json \
| jq -r '
  .items[]
  | {
      ns: .metadata.namespace,
      name: .metadata.name,
      type: .type,
      data: (.data // {})
    }
  | . as $s
  | ($s.data | to_entries[]? | {
      ns: $s.ns, name: $s.name, type: $s.type, key: .key, val: .value
    })
  | [ .ns, .name, .type, .key, (.val | @base64d) ]
  | @tsv
' > "$OUTFILE"

echo "Done: see all the secrets in $OUTFILE"
