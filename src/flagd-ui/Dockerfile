# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

FROM node:22@sha256:379c51ac7bbf9bffe16769cfda3eb027d59d9c66ac314383da3fcf71b46d026c AS builder

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends git=1:2.39.5-0+deb12u3 \
 && rm -rf /var/lib/apt/lists/*

COPY ./src/flagd-ui/package*.json ./

RUN npm ci

COPY ./src/flagd-ui/. ./

RUN npm run build

# -----------------------------------------------------------------------------

FROM node:22-alpine@sha256:e4bf2a82ad0a4037d28035ae71529873c069b13eb0455466ae0bc13363826e34
ARG FLAGD_UI_PORT=4000
ARG FLAGD_UI_JWT_SECRET="abcdefghijk0123456789lmnopqrstuv"

WORKDIR /app

COPY ./src/flagd-ui/package*.json ./

RUN npm ci --omit=dev

COPY --from=builder /app/src/instrumentation.ts ./instrumentation.ts
COPY --from=builder /app/next.config.mjs ./next.config.mjs

COPY --from=builder /app/.next ./.next

ENV FLAGD_UI_JWT_SECRET=${FLAGD_UI_JWT_SECRET} \
    FLAGD_UI_PORT=${FLAGD_UI_PORT}

EXPOSE ${FLAGD_UI_PORT}

CMD ["npm", "start"]
