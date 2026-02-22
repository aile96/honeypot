# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

FROM nginx:1.27.0-otel@sha256:4b1f896df9f04d104774212be385906d5228a8727445763266722cf76dcb2efd
ARG IMAGE_PROVIDER_PORT=8081
ARG OTEL_COLLECTOR_HOST="otel-collector"
ARG OTEL_COLLECTOR_PORT_GRPC=4317
ARG OTEL_SERVICE_NAME="otel-collector"

RUN apt-get update ; apt-get install lsb-release=12.0-1 --no-install-recommends --no-install-suggests -y

# This file is needed for nginx-module-otel to be found.
RUN echo "deb [signed-by=/usr/share/keyrings/nginx-archive-keyring.gpg] http://nginx.org/packages/mainline/debian `lsb_release -cs` nginx" | tee /etc/apt/sources.list.d/nginx.list

RUN apt-get update ; apt-get install nginx-module-otel=1.27.0+0.1.0-2~bookworm --no-install-recommends --no-install-suggests -y

RUN mkdir /static
COPY static /static

EXPOSE ${IMAGE_PROVIDER_PORT}

STOPSIGNAL SIGQUIT

COPY nginx.conf.template /nginx.conf.template

ENV OTEL_COLLECTOR_HOST=${OTEL_COLLECTOR_HOST} \
    IMAGE_PROVIDER_PORT=${IMAGE_PROVIDER_PORT} \
    OTEL_COLLECTOR_PORT_GRPC=${OTEL_COLLECTOR_PORT_GRPC} \
    OTEL_SERVICE_NAME=${OTEL_SERVICE_NAME}

# Start nginx
CMD ["/bin/sh","-c","envsubst '$$OTEL_COLLECTOR_HOST $$IMAGE_PROVIDER_PORT $$OTEL_COLLECTOR_PORT_GRPC $$OTEL_SERVICE_NAME' < /nginx.conf.template > /tmp/nginx.conf \
 && echo '--- Effective nginx.conf ---' && cat /tmp/nginx.conf && echo '----------------------------' \
 && exec nginx -g 'daemon off;' -c /tmp/nginx.conf"]
