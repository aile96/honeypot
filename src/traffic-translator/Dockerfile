# Build minimal, with grpcurl
FROM alpine:3.20

# Install dependencies + jq + grpcurl
RUN apk add --no-cache ca-certificates curl jq libc6-compat && \
    GRPCURL_VERSION=1.9.2 && \
    curl -L -o /tmp/grpcurl.tgz \
      https://github.com/fullstorydev/grpcurl/releases/download/v${GRPCURL_VERSION}/grpcurl_${GRPCURL_VERSION}_linux_x86_64.tar.gz && \
    tar -xzf /tmp/grpcurl.tgz -C /usr/local/bin grpcurl && \
    rm -f /tmp/grpcurl.tgz && \
    adduser -D -u 10001 app

WORKDIR /app
COPY main.go /app/

# Compiling Go standard via apk
RUN apk add --no-cache go build-base && \
    CGO_ENABLED=0 go build -o /usr/local/bin/http2grpc /app/main.go && \
    apk del go build-base

USER 10001:10001
ENV PORT=8080 DEFAULT_PLAINTEXT=true DEFAULT_TIMEOUT_S=10
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/http2grpc"]
