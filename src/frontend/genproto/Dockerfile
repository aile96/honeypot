# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

FROM node:20-alpine@sha256:09e2b3d9726018aecf269bd35325f46bf75046a643a66d28360ec71132750ec8

WORKDIR /app

RUN apk add --no-cache libc6-compat=1.1.0-r4 protobuf-dev=31.1-r1 protoc=31.1-r1

COPY ./src/frontend/package*.json ./

RUN npm ci

COPY ./pb /build/pb
