# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0
# Copyright 2023 The OpenTelemetry Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

FROM alpine:3.18@sha256:de0eb0b3f2a47ba1eb89389859a9bd88b28e82f5826b6969ad604979713c2d4f AS builder

RUN apk update && apk add git=2.40.4-r0 cmake=3.26.5-r0 make=4.4.1-r1 g++=12.2.1_git20220924-r10 grpc-dev=1.54.2-r0 protobuf-dev=3.21.12-r2 linux-headers=6.3-r0

COPY ./src/currency /currency
COPY ./pb/demo.proto /currency/proto/demo.proto

RUN cd /currency \
    && mkdir -p build && cd build \
    && cmake ../genproto \
    && make -j$(nproc || sysctl -n hw.ncpu || echo 1) demo-proto
