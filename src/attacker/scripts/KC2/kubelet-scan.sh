#!/usr/bin/env bash
set -eu

IP_FILE="/tmp/iphost"
OUT_DIR="$DATA_PATH/KC2/kubeletscan"
[ -f "${IP_FILE}" ] || { echo "IP file not found: ${IP_FILE}"; exit 1; }

TIMEOUT="5"
RATE_DELAY="0.2"
SAVE_BODY="0"
MAX_BYTES="5242880"
ENDPOINTS="/pods /metrics /stats/summary /spec /healthz"
PORT=10255
SCHEME="http"

# Installing dependencies and setup
apt-get update >/dev/null 2>&1
apt-get install -y --no-install-recommends bash curl gawk \
  sed coreutils jq ca-certificates >/dev/null 2>&1
mkdir -p "${OUT_DIR}"
SUMMARY="${OUT_DIR}/summary.ndjson"
REPORT="${OUT_DIR}/report.txt"
: > "${SUMMARY}"
: > "${REPORT}"

now_ts() { date +%s; }

sanitize() {
  # turn /stats/summary -> stats_summary
  printf "%s" "$1" | tr -c '[:alnum:]_-' '_' | sed 's/^_//; s/_$//'
}

probe_one() {
  host="$1"
  ep="$2"
  base="${OUT_DIR}/${host}/$(sanitize "${ep}")"
  mkdir -p "${base}"

  url="${SCHEME}://${host}:${PORT}${ep}"

  # temp files
  hdr="${base}/headers.txt"
  body="${base}/body.bin"
  meta="${base}/meta.txt"

  # do request
  code_size="$(curl -sS --max-time "${TIMEOUT}" -D "${hdr}" -o "${body}" -w "%{http_code} %{size_download}" "$url" 2>"${base}/curl.stderr" || true)"

  # parse results
  code="$(printf "%s" "${code_size}" | awk '{print $1}')"
  size="$(printf "%s" "${code_size}" | awk '{print $2}')"

  if [ -z "${code}" ]; then
    code="-1"
    size="0"
  fi

  saved_body_path=""
  if [ "${SAVE_BODY}" = "1" ] && [ -f "${body}" ]; then
    actual_size="$(wc -c < "${body}" | tr -d ' ')"
    if [ "${actual_size}" -gt "${MAX_BYTES}" ]; then
      tmp_cut="${body}.cut"
      dd if="${body}" of="${tmp_cut}" bs=1 count="${MAX_BYTES}" 2>/dev/null
      mv "${tmp_cut}" "${body}"
      printf "original_length=%s\nsaved=%s\n" "${actual_size}" "${MAX_BYTES}" > "${base}/truncated.info"
      saved_body_path="${body}"
    else
      saved_body_path="${body}"
    fi
  else
    rm -f "${body}"
  fi

  {
    echo "url=${url}"
    echo "status=${code}"
    echo "bytes_download=${size}"
    echo "timestamp=$(now_ts)"
  } > "${meta}"

  printf '%s\n' \
    "$(printf '{"host":"%s","endpoint":"%s","url":"%s","status":%s,"body_length":%s,"headers_path":"%s","body_path":"%s","ts":%s}' \
      "${host}" "${ep}" "${url}" "${code}" "${size}" "$(printf %s "${hdr}" | sed 's/"/\\"/g')" \
      "$(printf %s "${saved_body_path}" | sed 's/"/\\"/g')" "$(now_ts)")" \
    " " >> "${SUMMARY}"

  printf '%s\n' "$(printf '%-15s %-18s -> %3s  (%s bytes)' "${host}" "${ep}" "${code}" "${size}")" >> "${REPORT}"
}

# --- MAIN LOOP: read "IP - HOST" and filter on worker|control-plane ---
while IFS= read -r line || [ -n "$line" ]; do
  # trim e salta righe vuote/commenti
  line="$(printf "%s" "$line" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
  [ -z "${line}" ] && continue
  case "${line}" in \#*) continue ;; esac

  # Extract IP and HOST; Case-insensitive
  ip="$(printf "%s\n" "$line" | awk -F'[[:space:]]+-[[:space:]]+' 'NF>=1{print $1}')"
  host_name="$(printf "%s\n" "$line" | awk -F'[[:space:]]+-[[:space:]]+' 'NF>=2{print $2}')"
  name_lc="$(printf "%s" "$host_name" | tr '[:upper:]' '[:lower:]')"

  # Keep only worker or control-plane
  case "${name_lc}" in
    *worker*|*control-plane*) ;;
    *) continue ;;
  esac

  # Use IP as "host" for probe
  mkdir -p "${OUT_DIR}/${ip}"

  for ep in ${ENDPOINTS}; do
    probe_one "${ip}" "${ep}"
    sleep "${RATE_DELAY}" 2>/dev/null || sleep 1
  done
done < "${IP_FILE}"

echo "Summary written to: ${SUMMARY}"
echo "Human report:       ${REPORT}"
echo "Tip (needs jq): jq -r '. | select(type==\"object\") | [.host,.endpoint,\"\(.status)\"] | @tsv' ${SUMMARY}"
