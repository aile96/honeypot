# Build minimal, with grpcurl
FROM alpine:3.20@sha256:a4f4213abb84c497377b8544c81b3564f313746700372ec4fe84653e4fb03805

# Install dependencies + jq + grpcurl
RUN apk add --no-cache ca-certificates=20250911-r0 curl=8.14.1-r2 jq=1.7.1-r0 libc6-compat=1.1.0-r4 && \
    GRPCURL_VERSION=1.9.2 && \
    curl -L -o /tmp/grpcurl.tgz \
      https://github.com/fullstorydev/grpcurl/releases/download/v${GRPCURL_VERSION}/grpcurl_${GRPCURL_VERSION}_linux_x86_64.tar.gz && \
    tar -xzf /tmp/grpcurl.tgz -C /usr/local/bin grpcurl && \
    rm -f /tmp/grpcurl.tgz && \
    adduser -D -u 10001 app

WORKDIR /app
COPY go.mod /app/
COPY go.sum /app/
COPY main.go /app/

# Compiling Go standard via apk
RUN apk add --no-cache go=1.22.10-r0 build-base=0.5-r3 && \
    go mod download && \
    CGO_ENABLED=0 go build -o /usr/local/bin/http2grpc /app/main.go && \
    apk del go build-base

USER 10001:10001
ENV PORT=8080 DEFAULT_PLAINTEXT=true DEFAULT_TIMEOUT_S=10
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/http2grpc"]
