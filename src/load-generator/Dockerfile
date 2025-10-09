# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0


FROM python:3.12-slim-bookworm AS base

FROM base AS builder
RUN apt-get -qq update \
    && apt-get install -y --no-install-recommends g++ iproute2 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --prefix="/reqs" -r requirements.txt

FROM base
WORKDIR /usr/src/app/
COPY --from=builder /reqs /usr/local

RUN apt-get -qq update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

ENV PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers \
    LOCUST_USERS="20" \
    LOCUST_SPAWN_RATE="10" \
    LOCUST_HOST="http://172.18.0.200:8080" \
    LOCUST_HEADLESS="true" \
    LOCUST_AUTOSTART="true" \
    LOCUST_BROWSER_TRAFFIC_ENABLED="true"

RUN playwright install --with-deps chromium
COPY locustfile.py .
COPY people.json .

COPY entrypoint.sh .
ENTRYPOINT ["./entrypoint.sh"]
#ENTRYPOINT ["locust", "--skip-log-setup"]
