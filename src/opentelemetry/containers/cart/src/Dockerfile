# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0
# Copyright 2021 Google LLC
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

# https://mcr.microsoft.com/v2/dotnet/sdk/tags/list
FROM --platform=$BUILDPLATFORM mcr.microsoft.com/dotnet/sdk:8.0@sha256:58359d0b8fe8237be1d63ac335ca378e2857f976c7f92791f7c84b3c117037f5 AS builder
ARG TARGETARCH

WORKDIR /usr/src/app/

COPY ./src/ ./src/
COPY ./pb/ ./pb/

RUN dotnet restore ./src/cart.csproj -r linux-musl-$TARGETARCH

RUN dotnet publish ./src/cart.csproj -r linux-musl-$TARGETARCH --no-restore -o /cart

# -----------------------------------------------------------------------------

# https://mcr.microsoft.com/v2/dotnet/runtime-deps/tags/list
FROM mcr.microsoft.com/dotnet/runtime-deps:8.0-alpine3.20@sha256:584044431ade72d7af4fd7305462305a24b1bc6ae1bf3c2d96a41151ed39e9af
ARG CART_PORT=8080

WORKDIR /usr/src/app/
COPY --from=builder /cart/ ./

ENV DOTNET_HOSTBUILDER__RELOADCONFIGONCHANGE=false \
    CART_PORT=${CART_PORT}

EXPOSE ${CART_PORT}
ENTRYPOINT [ "./cart" ]
