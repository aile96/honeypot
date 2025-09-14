# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

FROM node:22 AS builder

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*

COPY ./src/flagd-ui/package*.json ./

RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

COPY ./src/flagd-ui/. ./

RUN npm run build

# -----------------------------------------------------------------------------

FROM node:22-alpine

WORKDIR /app

COPY ./src/flagd-ui/package*.json ./

RUN npm ci --omit=dev || npm install --omit=dev

COPY --from=builder /app/src/instrumentation.ts ./instrumentation.ts
COPY --from=builder /app/next.config.mjs ./next.config.mjs

COPY --from=builder /app/.next ./.next

ENV FLAGD_UI_JWT_SECRET="abcdefghijk0123456789lmnopqrstuv"

EXPOSE ${FLAGD_UI_PORT}

CMD ["npm", "start"]
