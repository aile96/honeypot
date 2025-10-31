# build
FROM golang:1.24 AS build
WORKDIR /src

# 1) copy mod and download base dependencies
COPY go.mod ./
RUN go mod download

# 2) copy the rest of the code, generate go.sum and compile
COPY . .
RUN go mod tidy
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath -ldflags="-s -w" -o /out/crypto-proxy .

# run (distroless)
FROM gcr.io/distroless/static-debian12

ARG CRYPTO_USE_FLAGD="true"
ARG CRYPTO_DEFAULT_WORD="my-fallback-secret"

ENV CRYPTO_USE_FLAGD="$CRYPTO_USE_FLAGD" \
    CRYPTO_DEFAULT_WORD="$CRYPTO_DEFAULT_WORD"
COPY --from=build /out/crypto-proxy /crypto-proxy
EXPOSE 18080 8081
ENTRYPOINT ["/crypto-proxy"]
