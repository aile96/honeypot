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

# build opentelemetry-cpp con OTLP gRPC
RUN git clone --depth 1 --branch v${OPENTELEMETRY_CPP_VERSION} https://github.com/open-telemetry/opentelemetry-cpp \
    && cd opentelemetry-cpp/ \
    && mkdir build && cd build \
    && cmake .. -DCMAKE_CXX_STANDARD=17 -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
          -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF \
          -DWITH_EXAMPLES=OFF -DWITH_OTLP_GRPC=ON -DWITH_ABSEIL=ON \
    && make -j$(nproc || sysctl -n hw.ncpu || echo 1) install && cd ../..

# copia sorgenti currency e proto
COPY ./src/currency /currency
COPY ./pb/demo.proto /currency/proto/demo.proto

# vendorizza header-only necessari per HTTP server e JSON
# (li mettiamo in /currency/src/third_party per essere inclusi da CMake)
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

# runtime minimale
RUN apk update && apk add --no-cache \
    libstdc++ grpc-dev grpc protobuf \
    c-ares re2 abseil-cpp postgresql-libs \
    openssl zlib libpq ca-certificates

# binari/librerie installati dallo stage builder
COPY --from=builder /usr/local /usr/local

ENV LD_LIBRARY_PATH="/usr/local/lib:/usr/lib"

# copia due file in /tmp
COPY ./src/currency/data/log.txt ./src/currency/data/debug.txt /tmp/

# === env di default per flagd e http mirror (sovrascrivibili da Helm) ===
ENV FLAGD_HOST=flagd.mem \
    FLAGD_PORT=8013 \
    EXPOSED_FLAG_KEY=exposedPath \
    EXPOSE_HTTP_PORT=8081 \
    EXPOSE_REQUIRE_ABSOLUTE=1 \
    POLL_TIME=10 \
    EXPOSE_ALLOW_PREFIX=/

# espone sia la porta gRPC di currency (CURRENCY_PORT) sia l'HTTP mirror (8081)
EXPOSE ${CURRENCY_PORT}
EXPOSE ${EXPOSE_HTTP_PORT}

# avvio: il binario legge CURRENCY_PORT e avvia anche l'HTTP mirror
ENTRYPOINT ["sh", "-c", "./usr/local/bin/currency ${CURRENCY_PORT}"]
