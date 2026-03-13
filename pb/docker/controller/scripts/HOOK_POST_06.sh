#!/usr/bin/env bash
set -Eeuo pipefail

CHECKOUT_TAG_TO_DELETE="2.0.3"
FRONTEND_PROXY_TAG_TO_DELETE="2.0.2"

log() {
  printf '[HOOK_POST_06] %s\n' "$*"
}

warn() {
  printf '[HOOK_POST_06][WARN] %s\n' "$*" >&2
}

copy_kc6_results() {
  local data_path

  : "${ATT_OUT:?ATT_OUT must be set}"
  data_path="$(docker exec "${ATT_OUT}" printenv DATA_PATH)"
  mkdir -p /results
  docker cp "${ATT_OUT}:${data_path}/KC6" /results/
}

resolve_manifest() {
  local image_name="$1"
  local image_tag="$2"
  local base url headers digest

  for base in "${REGISTRY_BASES[@]}"; do
    url="${base}/v2/${image_name}/manifests/${image_tag}"
    headers="$(curl -sSIk "${REGISTRY_AUTH_ARGS[@]}" \
      -H 'Accept: application/vnd.docker.distribution.manifest.v2+json' \
      "${url}" 2>/dev/null || true)"
    digest="$(printf '%s\n' "${headers}" | tr -d '\r' | awk -F': ' 'tolower($1)=="docker-content-digest"{print $2; exit}')"
    if [[ -n "${digest}" ]]; then
      printf '%s|%s\n' "${base}" "${digest}"
      return 0
    fi
  done

  return 1
}

delete_registry_tag() {
  local image_name="$1"
  local image_tag="$2"
  local resolved base digest status

  resolved="$(resolve_manifest "${image_name}" "${image_tag}" || true)"
  if [[ -z "${resolved}" ]]; then
    log "Tag ${image_name}:${image_tag} not found in registry."
    return 0
  fi

  IFS='|' read -r base digest <<< "${resolved}"
  status="$(curl -sSk -o /dev/null -w '%{http_code}' -X DELETE \
    "${REGISTRY_AUTH_ARGS[@]}" \
    "${base}/v2/${image_name}/manifests/${digest}")"

  case "${status}" in
    202) log "Deleted ${image_name}:${image_tag} from ${base}." ;;
    404) log "Tag already absent for ${image_name}:${image_tag}." ;;
    *) warn "Delete failed for ${image_name}:${image_tag} (HTTP ${status})." ;;
  esac
}

main() {
  local registry_host
  local image_name image_tag

  : "${HOSTREGISTRY:?HOSTREGISTRY must be set}"

  copy_kc6_results

  registry_host="${HOSTREGISTRY#https://}"
  registry_host="${registry_host#http://}"
  REGISTRY_BASES=("https://${registry_host}" "http://${registry_host}")

  REGISTRY_AUTH_ARGS=()
  if [[ -n "${REGISTRY_USER:-}" && -n "${REGISTRY_PASS:-}" ]]; then
    REGISTRY_AUTH_ARGS=(-u "${REGISTRY_USER}:${REGISTRY_PASS}")
  else
    warn "REGISTRY_USER/REGISTRY_PASS not set: trying unauthenticated registry cleanup."
  fi

  for image_name in "checkout" "frontend-proxy"; do
    case "${image_name}" in
      checkout) image_tag="${CHECKOUT_TAG_TO_DELETE}" ;;
      frontend-proxy) image_tag="${FRONTEND_PROXY_TAG_TO_DELETE}" ;;
      *) continue ;;
    esac
    delete_registry_tag "${image_name}" "${image_tag}"
  done
}

main "$@"
