# init-from-json.py
import json
import sys
from pathlib import Path

# INPUT atteso
SRC = Path("payments.json")
# OUTPUT SQL (puoi cambiarlo se preferisci)
OUT = Path("init.sql")

def esc(s: str) -> str:
    # escape minimale dei singoli apici per literal SQL
    return s.replace("'", "''")

def lit(v):
    """Ritorna un literal SQL corretto oppure NULL."""
    if v is None:
        return "NULL"
    s = str(v).strip()
    if s == "":
        return "NULL"
    return f"'{esc(s)}'"

def as_int(v, field):
    if v is None or str(v).strip() == "":
        raise ValueError(f"Campo {field} mancante")
    try:
        return int(v)
    except Exception:
        raise ValueError(f"Campo {field} non numerico: {v!r}")

try:
    raw = SRC.read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError("payments.json deve essere una lista di oggetti pagamento")
except Exception as e:
    print(f"Errore leggendo {SRC}: {e}", file=sys.stderr)
    raise

with OUT.open("w", encoding="utf-8") as out:
    out.write("""\
-- Auto-generato da init-from-json.py
-- Schema per metodi di pagamento (ATTENZIONE: dati in chiaro, NON usare in produzione)
CREATE TABLE IF NOT EXISTS payments (
  id SERIAL PRIMARY KEY,
  user_id VARCHAR(128) NOT NULL UNIQUE,
  card_holder_name TEXT NOT NULL,
  card_number TEXT NOT NULL,   -- in chiaro
  exp_month SMALLINT NOT NULL,
  exp_year SMALLINT NOT NULL,
  cvv TEXT NOT NULL,           -- in chiaro
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- trigger updated_at
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_set_updated_at ON payments;
CREATE TRIGGER trg_set_updated_at
BEFORE UPDATE ON payments
FOR EACH ROW EXECUTE PROCEDURE set_updated_at();

-- indici
CREATE UNIQUE INDEX IF NOT EXISTS idx_payments_user ON payments (user_id);

""")

    valid = 0
    for i, p in enumerate(data, 1):
        # campi attesi
        user_id_raw        = p.get("user_id")
        card_holder_name   = p.get("card_holder_name")
        card_number        = p.get("card_number")
        exp_month_raw      = p.get("exp_month")
        exp_year_raw       = p.get("exp_year")
        cvv                = p.get("cvv")

        # normalizzazione
        if isinstance(user_id_raw, str):
            user_id_raw = user_id_raw.strip() or None
        if isinstance(card_holder_name, str):
            card_holder_name = card_holder_name.strip() or None
        if isinstance(card_number, str):
            card_number = card_number.strip() or None
        if isinstance(cvv, str):
            cvv = cvv.strip() or None

        # validazioni minime
        if not (user_id_raw and card_holder_name and card_number and cvv):
            # record incompleto → salta
            continue
        try:
            exp_month = as_int(exp_month_raw, "exp_month")
            exp_year  = as_int(exp_year_raw, "exp_year")
        except ValueError as ve:
            print(f"[riga {i}] salto record: {ve}", file=sys.stderr)
            continue
        if not (1 <= exp_month <= 12):
            print(f"[riga {i}] salto record: exp_month fuori range (1-12): {exp_month}", file=sys.stderr)
            continue

        # literal SQL
        user_id  = esc(str(user_id_raw))
        holder   = esc(str(card_holder_name))
        number   = esc(str(card_number))
        cvv_lit  = esc(str(cvv))

        out.write(
            "INSERT INTO payments (user_id, card_holder_name, card_number, exp_month, exp_year, cvv) VALUES "
            f"('{user_id}', '{holder}', '{number}', {exp_month}, {exp_year}, '{cvv_lit}') "
            "ON CONFLICT (user_id) DO UPDATE SET "
            "card_holder_name = EXCLUDED.card_holder_name, "
            "card_number = EXCLUDED.card_number, "
            "exp_month = EXCLUDED.exp_month, "
            "exp_year = EXCLUDED.exp_year, "
            "cvv = EXCLUDED.cvv, "
            "updated_at = NOW();\n"
        )
        valid += 1

print(f"Generato {OUT} con {valid} record validi (gli altri sono stati saltati).")
