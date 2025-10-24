# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0
FROM envoyproxy/envoy:v1.32-latest

ARG CALDERA_HOST="prova"
ARG CALDERA_PORT="8080"

RUN apt-get update && apt-get install -y gettext-base && apt-get clean && rm -rf /var/lib/apt/lists/*

USER envoy
WORKDIR /home/envoy
COPY envoy.tmpl.yaml envoy.tmpl.yaml

ENV CALDERA_HOST="$CALDERA_HOST" \
    CALDERA_PORT="$CALDERA_PORT"

ENTRYPOINT ["/bin/sh","-c","envsubst < envoy.tmpl.yaml > /tmp/envoy.yaml && envoy -c /tmp/envoy.yaml"]