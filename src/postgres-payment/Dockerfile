# Stage 1: builder che genera init.sql da JSON
FROM python:3.11-alpine AS builder

COPY payments.json /tmp/
COPY init-from-json.py /tmp/

WORKDIR /tmp
RUN python3 /tmp/init-from-json.py
# /tmp/init.sql ora è pronto

# Stage 2: immagine finale con Postgres
FROM postgres:16-alpine

# opzionale: locale/encoding (Postgres default va bene)
COPY --from=builder /tmp/init.sql /docker-entrypoint-initdb.d/001-init.sql
