# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0


FROM --platform=${BUILDPLATFORM} gradle:8-jdk17@sha256:412514b302cea3d3ba1df8c44f838292e0c9cdc816beabee6beb160a4b7a49f1 AS builder

ARG OTEL_JAVA_AGENT_VERSION="2.15.0"
WORKDIR /usr/src/app/

COPY ./ ./

RUN gradle shadowJar
ADD --chmod=0644 https://github.com/open-telemetry/opentelemetry-java-instrumentation/releases/download/v$OTEL_JAVA_AGENT_VERSION/opentelemetry-javaagent.jar /usr/src/app/opentelemetry-javaagent.jar
RUN chmod 0644 /usr/src/app/opentelemetry-javaagent.jar

# -----------------------------------------------------------------------------

FROM gcr.io/distroless/java17-debian12:nonroot@sha256:04bb15d5629b31d26e7deaa020112a24aad878a606a4a9b99a3cae9d41f26b36

WORKDIR /usr/src/app/

ENV JAVA_TOOL_OPTIONS=-javaagent:/usr/src/app/opentelemetry-javaagent.jar \
    OTEL_LOGS_EXPORTER=otlp

COPY --from=builder /usr/src/app/build/libs/fraud-detection-1.0-all.jar fraud-detection-1.0-all.jar
COPY --from=builder --chown=1000:1000 --chmod=0644 /usr/src/app/opentelemetry-javaagent.jar opentelemetry-javaagent.jar

ENTRYPOINT [ "java", "-jar", "fraud-detection-1.0-all.jar" ]
