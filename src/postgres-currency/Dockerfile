# Stage 1: builder per generare init.sql da JSON
FROM python:3.11-alpine AS builder

COPY currency_conversion.json /tmp/
COPY init-from-json.py /tmp/

WORKDIR /tmp
RUN python3 /tmp/init-from-json.py
# /tmp/init.sql ora è disponibile

# Stage 2: immagine finale con Postgres
FROM postgres:15-alpine

COPY --from=builder /tmp/init.sql /docker-entrypoint-initdb.d/init.sql
