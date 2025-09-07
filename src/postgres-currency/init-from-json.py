import json

with open("currency_conversion.json", "r") as f:
    data = json.load(f)

with open("init.sql", "w") as out:
    out.write("""
CREATE TABLE IF NOT EXISTS currency_rates (
  code TEXT PRIMARY KEY,
  rate DOUBLE PRECISION NOT NULL
);\n\n""")

    for code, rate in data.items():
        out.write(f"INSERT INTO currency_rates (code, rate) VALUES ('{code}', {rate}) "
                  f"ON CONFLICT (code) DO NOTHING;\n")
