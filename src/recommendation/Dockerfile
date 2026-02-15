# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0


FROM python:3.12-alpine3.21@sha256:d4f9227f21409479c7fe92288f2e40b1a56ae591c8ad3b5446dfeaef90f67857 AS build-venv

RUN apk update && \
    apk add gcc=14.2.0-r4 g++=14.2.0-r4 linux-headers=6.6-r1

COPY ./src/recommendation/requirements.txt requirements.txt

RUN python -m venv venv && \
    venv/bin/pip install --no-cache-dir -r requirements.txt

RUN venv/bin/pip install --no-cache-dir opentelemetry-instrumentation-grpc==0.51b0 opentelemetry-instrumentation-logging==0.51b0 opentelemetry-instrumentation-system-metrics==0.51b0

FROM python:3.12-alpine3.21@sha256:d4f9227f21409479c7fe92288f2e40b1a56ae591c8ad3b5446dfeaef90f67857
ARG RECOMMENDATION_PORT=8080

COPY --from=build-venv /venv/ /venv/

WORKDIR /app

COPY ./src/recommendation/demo_pb2_grpc.py demo_pb2_grpc.py
COPY ./src/recommendation/demo_pb2.py demo_pb2.py
COPY ./src/recommendation/logger.py logger.py
COPY ./src/recommendation/metrics.py metrics.py
COPY ./src/recommendation/recommendation_server.py recommendation_server.py

ENV RECOMMENDATION_PORT=${RECOMMENDATION_PORT}

EXPOSE ${RECOMMENDATION_PORT}
ENTRYPOINT [ "/venv/bin/opentelemetry-instrument", "/venv/bin/python", "recommendation_server.py" ]
