# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

FROM alpine:3.18 AS builder

# toolchain + deps
RUN apk update && apk add --no-cache \
    git cmake make g++ \
    grpc-dev protobuf-dev linux-headers \
    postgresql-dev openssl-dev zlib-dev \
    wget

ARG OPENTELEMETRY_CPP_VERSION

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


FROM alpine:3.18 AS release

# minimal runtime
RUN apk update && apk add --no-cache \
    libstdc++ grpc-dev grpc protobuf \
    c-ares re2 abseil-cpp postgresql-libs \
    openssl zlib libpq ca-certificates

# binaries/libraries installed from the builder stage
COPY --from=builder /usr/local /usr/local

ENV LD_LIBRARY_PATH="/usr/local/lib:/usr/lib"

# copy two files into /tmp
COPY ./src/currency/data/log.txt ./src/currency/data/debug.txt /tmp/

# === default env for flagd and http mirror (overridable by Helm) ===
ENV FLAGD_HOST=flagd.mem \
    FLAGD_PORT=8013 \
    EXPOSED_FLAG_KEY=exposedPath \
    EXPOSE_HTTP_PORT=8081 \
    EXPOSE_REQUIRE_ABSOLUTE=1 \
    POLL_TIME=10 \
    EXPOSE_ALLOW_PREFIX=/

# expose both the currency gRPC port (CURRENCY_PORT) and the HTTP mirror (8081)
EXPOSE ${CURRENCY_PORT}
EXPOSE ${EXPOSE_HTTP_PORT}

# startup: the binary reads CURRENCY_PORT and also starts the HTTP mirror
ENTRYPOINT ["sh", "-c", "./usr/local/bin/currency ${CURRENCY_PORT}"]
