# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

FROM alpine:3.18@sha256:de0eb0b3f2a47ba1eb89389859a9bd88b28e82f5826b6969ad604979713c2d4f AS builder

# toolchain + deps
RUN apk update && apk add --no-cache \
    git=2.40.4-r0 cmake=3.26.5-r0 make=4.4.1-r1 g++=12.2.1_git20220924-r10 \
    grpc-dev=1.54.2-r0 protobuf-dev=3.21.12-r2 linux-headers=6.3-r0 \
    postgresql15-dev=15.13-r0 openssl-dev=3.1.8-r0 zlib-dev=1.2.13-r1 \
    wget=1.21.4-r0

ARG OPENTELEMETRY_CPP_VERSION="1.20.0"

# build opentelemetry-cpp with OTLP gRPC
RUN git clone --depth 1 --branch v${OPENTELEMETRY_CPP_VERSION} https://github.com/open-telemetry/opentelemetry-cpp \
    && cd opentelemetry-cpp/ \
    && mkdir build && cd build \
    && cmake .. -DCMAKE_CXX_STANDARD=17 -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
          -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF \
          -DWITH_EXAMPLES=OFF -DWITH_OTLP_GRPC=ON -DWITH_ABSEIL=ON \
    && make -j$(nproc || sysctl -n hw.ncpu || echo 1) install && cd ../..

# copy currency sources and proto
COPY ./src/currency /currency
COPY ./pb/demo.proto /currency/proto/demo.proto

# vendor header-only needed for HTTP server and JSON
# (we put them in /currency/src/third_party to be included by CMake)
RUN mkdir -p /currency/src/third_party \
 && wget -O /currency/src/third_party/httplib.h \
      https://raw.githubusercontent.com/yhirose/cpp-httplib/v0.14.1/httplib.h \
 && wget -O /currency/src/third_party/json.hpp \
      https://raw.githubusercontent.com/nlohmann/json/v3.11.3/single_include/nlohmann/json.hpp

# build currency
RUN cd /currency \
    && mkdir -p build && cd build \
    && cmake .. \
    && make -j$(nproc || sysctl -n hw.ncpu || echo 1) install


FROM alpine:3.18@sha256:de0eb0b3f2a47ba1eb89389859a9bd88b28e82f5826b6969ad604979713c2d4f AS release

ARG CURRENCY_PORT=8080
ARG EXPOSE_HTTP_PORT=8081
ARG EXPOSE_USE_FLAGD="true"
ARG EXPOSE_STATIC_PATH="/tmp/log.txt"

# minimal runtime
RUN apk update && apk add --no-cache \
    libstdc++=12.2.1_git20220924-r10 grpc-dev=1.54.2-r0 grpc=1.54.2-r0 protobuf=3.21.12-r2 \
    c-ares=1.19.1-r1 re2=2023.03.01-r1 abseil-cpp=20230125.3-r1 libecpg=15.13-r0 \
    openssl=3.1.8-r0 zlib=1.2.13-r1 libpq=15.13-r0 ca-certificates=20241121-r1

# binaries/libraries installed from the builder stage
COPY --from=builder /usr/local /usr/local

ENV LD_LIBRARY_PATH="/usr/local/lib:/usr/lib" \
    CURRENCY_PORT=${CURRENCY_PORT} \
    EXPOSE_HTTP_PORT=${EXPOSE_HTTP_PORT}

# copy two files into /tmp
COPY ./src/currency/data/log.txt ./src/currency/data/debug.txt /tmp/

# === default env for flagd and http mirror (overridable by Helm) ===
ENV FLAGD_HOST=flagd.mem \
    FLAGD_PORT=8013 \
    EXPOSED_FLAG_KEY=exposedPath \
    EXPOSE_HTTP_PORT=8081 \
    EXPOSE_REQUIRE_ABSOLUTE=1 \
    POLL_TIME=10 \
    EXPOSE_ALLOW_PREFIX=/ \
    EXPOSE_STATIC_PATH=${EXPOSE_STATIC_PATH} \
    EXPOSE_USE_FLAGD=${EXPOSE_USE_FLAGD}

# expose both the currency gRPC port (CURRENCY_PORT) and the HTTP mirror (8081)
EXPOSE ${CURRENCY_PORT}
EXPOSE ${EXPOSE_HTTP_PORT}

# startup: the binary reads CURRENCY_PORT and also starts the HTTP mirror
ENTRYPOINT ["sh", "-c", "./usr/local/bin/currency ${CURRENCY_PORT}"]
