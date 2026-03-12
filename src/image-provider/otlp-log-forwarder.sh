#!/bin/sh
# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0
set -eu

SERVICE_NAME="${OTEL_SERVICE_NAME:-image-provider}"
RAW_ENDPOINT="${OTEL_EXPORTER_OTLP_LOGS_ENDPOINT:-}"

if [ -z "${RAW_ENDPOINT}" ]; then
  HOST="${OTEL_COLLECTOR_HOST:-otel-collector}"
  PORT="${OTEL_COLLECTOR_PORT_HTTP:-4318}"
  RAW_ENDPOINT="http://${HOST}:${PORT}/v1/logs"
fi

normalize_endpoint() {
  endpoint="$1"
  case "${endpoint}" in
    http://*|https://*)
      ;;
    *)
      endpoint="http://${endpoint}"
      ;;
  esac

  case "${endpoint}" in
    */v1/logs)
      printf '%s' "${endpoint}"
      ;;
    *)
      endpoint="${endpoint%/}/v1/logs"
      printf '%s' "${endpoint}"
      ;;
  esac
}

ENDPOINT="$(normalize_endpoint "${RAW_ENDPOINT}")"

escape_json() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

severity_from_line() {
  line_lc="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"
  case "${line_lc}" in
    *panic*|*fatal*)
      printf 'FATAL|21'
      ;;
    *error*|*failed*)
      printf 'ERROR|17'
      ;;
    *warn*)
      printf 'WARN|13'
      ;;
    *debug*)
      printf 'DEBUG|5'
      ;;
    *)
      printf 'INFO|9'
      ;;
  esac
}

while IFS= read -r line || [ -n "${line}" ]; do
  [ -z "${line}" ] && continue

  ts_nano="$(date +%s%N)"
  escaped_line="$(escape_json "${line}")"
  severity="$(severity_from_line "${line}")"
  severity_text="${severity%|*}"
  severity_number="${severity#*|}"

  payload=$(cat <<EOF
{"resourceLogs":[{"resource":{"attributes":[{"key":"service.name","value":{"stringValue":"${SERVICE_NAME}"}}]},"scopeLogs":[{"scope":{"name":"nginx.file.forwarder"},"logRecords":[{"timeUnixNano":"${ts_nano}","observedTimeUnixNano":"${ts_nano}","severityText":"${severity_text}","severityNumber":${severity_number},"body":{"stringValue":"${escaped_line}"}}]}]}]}
EOF
)

  curl -sS --max-time 2 -H "Content-Type: application/json" -d "${payload}" "${ENDPOINT}" >/dev/null 2>&1 || true
done
