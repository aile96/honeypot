FROM alpine:latest

RUN apk add --no-cache bash curl jq kubectl

COPY update-images.sh /scripts/update-images.sh
RUN chmod +x /scripts/update-images.sh

ENTRYPOINT ["/scripts/update-images.sh"]