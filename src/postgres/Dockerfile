# Stage 1: builder that generates init.sql from JSON
FROM python:3.11-alpine AS builder

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
FROM postgres:16-alpine
COPY --from=builder /tmp/init.sql /docker-entrypoint-initdb.d/001-init.sql
