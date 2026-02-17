# build
FROM golang:1.24@sha256:d2d2bc1c84f7e60d7d2438a3836ae7d0c847f4888464e7ec9ba3a1339a1ee804 AS build
WORKDIR /src

# 1) copy module manifests and download exact dependencies
COPY go.mod go.sum ./
RUN go mod download

# 2) copy the rest of the code and compile
COPY . .
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath -ldflags="-s -w" -o /out/crypto-proxy .

# run (distroless)
FROM gcr.io/distroless/static-debian12@sha256:20bc6c0bc4d625a22a8fde3e55f6515709b32055ef8fb9cfbddaa06d1760f838

ARG CRYPTO_USE_FLAGD="true"
ARG CRYPTO_DEFAULT_WORD="my-fallback-secret"

ENV CRYPTO_USE_FLAGD="$CRYPTO_USE_FLAGD" \
    CRYPTO_DEFAULT_WORD="$CRYPTO_DEFAULT_WORD"
COPY --from=build /out/crypto-proxy /crypto-proxy
EXPOSE 18080 8081
ENTRYPOINT ["/crypto-proxy"]
