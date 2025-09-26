# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0


FROM python:3.12-slim-bookworm AS base

FROM base AS builder
RUN apt-get -qq update \
    && apt-get install -y --no-install-recommends g++ iproute2 \
    && rm -rf /var/lib/apt/lists/*

COPY ./src/load-generator/requirements.txt .
RUN pip install --prefix="/reqs" -r requirements.txt

FROM base
WORKDIR /usr/src/app/
COPY --from=builder /reqs /usr/local
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers
RUN playwright install --with-deps chromium
COPY ./src/load-generator/locustfile.py .
COPY ./src/load-generator/people.json .

ENV OTEL_COLLECTOR_NAME="otel-collector.mem" \
    OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE="cumulative" \
    OTEL_SERVICE_NAME="" \
    SERVICE_NAMESPACE="opentelemetry-demo" \
    APP_VERSION="dev" \
    LOCUST_WEB_HOST="0.0.0.0" \
    LOCUST_WEB_PORT="8089" \
    LOCUST_USERS="10" \
    LOCUST_SPAWN_RATE="1" \
    LOCUST_HOST="http://172.18.0.200:8080" \
    LOCUST_HEADLESS="false" \
    LOCUST_AUTOSTART="true" \
    LOCUST_BROWSER_TRAFFIC_ENABLED="true" \
    PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION="python" \
    FLAGD_HOST="flagd.mem" \
    FLAGD_OFREP_PORT="8016" \
    OTEL_EXPORTER_OTLP_ENDPOINT="http://otel-collector.mem:4317"

EXPOSE 8089

ENTRYPOINT ["locust", "--skip-log-setup"]
