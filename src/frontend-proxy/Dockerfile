# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

FROM envoyproxy/envoy:v1.32-latest@sha256:6bf0d37dd5e8eac4b37effe89a1aec4760f7f2ce860d1fd6af22147eb87a5058
RUN apt-get update && apt-get install -y gettext-base=0.21-4ubuntu4 && apt-get clean && rm -rf /var/lib/apt/lists/*

USER envoy
WORKDIR /home/envoy
COPY ./src/frontend-proxy/envoy.tmpl.yaml envoy.tmpl.yaml

ENTRYPOINT ["/bin/sh","-c","envsubst < envoy.tmpl.yaml > /tmp/envoy.yaml && envoy -c /tmp/envoy.yaml"]