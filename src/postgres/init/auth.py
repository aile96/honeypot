# init-from-json.py
import json
import sys
from pathlib import Path

SRC = Path("auth.json")
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

try:
    raw = SRC.read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError("users.json deve essere una lista di oggetti utente")
except Exception as e:
    print(f"Errore leggendo {SRC}: {e}", file=sys.stderr)
    raise

with OUT.open("w", encoding="utf-8") as out:
    out.write("""\
-- Auto-generato da init-from-json.py
-- Schema minimo per test (password in chiaro, NON usare in produzione)
CREATE TABLE IF NOT EXISTS users (
  id SERIAL PRIMARY KEY,
  username TEXT NOT NULL,
  password TEXT NOT NULL,
  email TEXT,
  address TEXT,
  city TEXT,
  state TEXT,
  zip TEXT,
  country TEXT,
  phone TEXT
);

-- vincoli/indici
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username ON users (username);
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users (email);
CREATE INDEX IF NOT EXISTS idx_users_city ON users (city);
CREATE INDEX IF NOT EXISTS idx_users_state ON users (state);
CREATE INDEX IF NOT EXISTS idx_users_zip ON users (zip);

""")

    for u in data:
        # campi attesi nel nuovo formato
        username_raw = u.get("username", "")
        password_raw = u.get("password", "")
        email_raw    = u.get("email")
        address_raw  = u.get("address")
        city_raw     = u.get("city")
        state_raw    = u.get("state")
        zip_raw      = u.get("zip")
        country_raw  = u.get("country")
        phone_raw    = u.get("phone")

        # normalizzazione semplice
        if isinstance(state_raw, str):
            state_raw = state_raw.strip().upper() or None
        if isinstance(zip_raw, str):
            zip_raw = zip_raw.strip() or None
        if isinstance(country_raw, str):
            country_raw = country_raw.strip() or None

        # salta record senza credenziali minime
        if not username_raw or not password_raw:
            continue

        # literal SQL
        username = esc(str(username_raw))
        password = esc(str(password_raw))
        email    = lit(email_raw)
        address  = lit(address_raw)
        city     = lit(city_raw)
        state    = lit(state_raw)
        zipc     = lit(zip_raw)
        country  = lit(country_raw)
        phone    = lit(phone_raw)

        # upsert su username (aggiorna gli altri campi)
        out.write(
            "INSERT INTO users (username, password, email, address, city, state, zip, country, phone) VALUES "
            f"('{username}', '{password}', {email}, {address}, {city}, {state}, {zipc}, {country}, {phone}) "
            "ON CONFLICT (username) DO UPDATE SET "
            "password = EXCLUDED.password, "
            "email = EXCLUDED.email, "
            "address = EXCLUDED.address, "
            "city = EXCLUDED.city, "
            "state = EXCLUDED.state, "
            "zip = EXCLUDED.zip, "
            "country = EXCLUDED.country, "
            "phone = EXCLUDED.phone;\n"
        )

print(f"Generato {OUT} con {len(data)} record (quelli senza username/password sono stati saltati).")
