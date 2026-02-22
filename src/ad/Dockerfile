# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0


FROM --platform=${BUILDPLATFORM} eclipse-temurin:21-jdk@sha256:2dbb970461f444a3230facb0a34ca9f2e555150689f0d943c252a3785c905487 AS builder
ARG _JAVA_OPTIONS
WORKDIR /usr/src/app/

COPY ./gradlew* ./settings.gradle* ./build.gradle ./
COPY ./gradle ./gradle

RUN chmod +x ./gradlew
RUN ./gradlew
RUN ./gradlew downloadRepos

COPY ./ ./
RUN chmod +x ./gradlew
RUN ./gradlew installDist -PprotoSourceDir=./proto

# -----------------------------------------------------------------------------

FROM eclipse-temurin:21-jre@sha256:05b34d0c037b1b216530d61a131797d2005f3fc8b24a50da5af4ec2df1009356

ARG OTEL_JAVA_AGENT_VERSION=2.15.0
ARG _JAVA_OPTIONS
ARG AD_PORT=8080

WORKDIR /usr/src/app/

COPY --from=builder /usr/src/app/ ./
ADD --chmod=644 https://github.com/open-telemetry/opentelemetry-java-instrumentation/releases/download/v$OTEL_JAVA_AGENT_VERSION/opentelemetry-javaagent.jar /usr/src/app/opentelemetry-javaagent.jar
ENV JAVA_TOOL_OPTIONS=-javaagent:/usr/src/app/opentelemetry-javaagent.jar \
    AD_PORT=${AD_PORT}

EXPOSE ${AD_PORT}
ENTRYPOINT [ "./build/install/opentelemetry-demo-ad/bin/Ad" ]
