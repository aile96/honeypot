# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0


FROM node:22-alpine AS build

WORKDIR /usr/src/app/

COPY ./src/payment/package*.json ./

RUN apk add --no-cache python3 make g++ && npm ci --omit=dev

# -----------------------------------------------------------------------------

FROM node:22-alpine
ARG PAYMENT_PORT=8080

USER node
WORKDIR /usr/src/app/
ENV NODE_ENV=production \
    PAYMENT_PORT=${PAYMENT_PORT}

COPY --chown=node:node --from=build /usr/src/app/node_modules/ ./node_modules/
COPY ./src/payment/ ./
COPY ./pb/demo.proto ./

EXPOSE ${PAYMENT_PORT}
ENTRYPOINT [ "npm", "run", "start" ]
