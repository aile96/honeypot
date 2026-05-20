# Stage 1: builder that generates init.sql from JSON
FROM python:3.11-alpine@sha256:303398d5c9f110790bce60d64f902e51e1a061e33292985c72bf6cd07960bf09 AS builder

ARG DB=curr

COPY init/ /tmp/init/
COPY json/ /tmp/json/

WORKDIR /tmp

RUN set -eux; \
    test -f "/tmp/json/${DB}.json"; \
    test -f "/tmp/init/${DB}.py"; \
    mv "/tmp/json/${DB}.json" /tmp/${DB}.json; \
    mv "/tmp/init/${DB}.py" /tmp/${DB}.py; \
    python3 /tmp/${DB}.py; rm -rf /tmp/json /tmp/init
# /tmp/init.sql is now ready

# Stage 2: final image with Postgres
FROM postgres:16-alpine@sha256:97ff59a4e30e08d1c11bdcd9455e7832368c0572b576c9092cde2df4ae5552a3
COPY --from=builder /tmp/init.sql /docker-entrypoint-initdb.d/001-init.sql
