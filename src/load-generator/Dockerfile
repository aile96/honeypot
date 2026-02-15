# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0


FROM python:3.12-slim-bookworm@sha256:73dbd1a2ad74137451593a8ac30f7bdd0f5010fc05fb34644190cffa7696bbf3 AS base

FROM base AS builder
RUN apt-get -qq update \
    && apt-get install -y --no-install-recommends g++=4:12.2.0-3 iproute2=6.1.0-3 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --prefix="/reqs" -r requirements.txt

FROM base
WORKDIR /usr/src/app/
COPY --from=builder /reqs /usr/local

RUN apt-get -qq update \
 && apt-get install -y --no-install-recommends curl=7.88.1-10+deb12u14 ca-certificates=20230311+deb12u1 bash=5.2.15-2+b10 \
 && rm -rf /var/lib/apt/lists/*

ENV PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers \
    LOCUST_USERS="20" \
    LOCUST_SPAWN_RATE="10" \
    LOCUST_HOST="http://172.18.0.200:8080" \
    LOCUST_HEADLESS="true" \
    LOCUST_AUTOSTART="true" \
    LOCUST_BROWSER_TRAFFIC_ENABLED="true"

RUN python -m playwright install --with-deps chromium
COPY locustfile.py .
COPY people.json .

COPY entrypoint.sh .
RUN sed -i 's/\r$//' entrypoint.sh && chmod +x entrypoint.sh

# Use exec form and call via bash (works even if x-bit ever gets lost)
ENTRYPOINT ["/usr/bin/env", "bash", "/usr/src/app/entrypoint.sh"]
