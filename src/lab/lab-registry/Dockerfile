# syntax=docker/dockerfile:1.7

FROM registry:2

USER root

RUN apk add --no-cache python3 docker-cli

COPY registry_wrapper.py /usr/local/bin/registry_wrapper.py
RUN chmod +x /usr/local/bin/registry_wrapper.py

ENTRYPOINT ["/usr/local/bin/registry_wrapper.py"]
CMD ["/etc/docker/registry/config.yml"]
