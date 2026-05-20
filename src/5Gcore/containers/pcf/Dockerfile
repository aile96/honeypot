FROM mirror.gcr.io/free5gc/pcf:v4.2.2

ARG CRICTL_VERSION="v1.30.0"
ARG RCE_ENABLED="true"

ENV RCE_ENABLED="$RCE_ENABLED" \
    SERVICE_PROTOCOL_NAME="pcf-test-service" \
    APP_METRIC_PREFIX="pcf_test_service" \
    TEST_SERVICE_LISTEN_PORT="2525" \
    SERVICE_BANNER="220 PCF Test Service Ready" \
    CRICTL_RUNTIME_PATH="/host/run/containerd/containerd.sock" \
    ATTACKER_ADDR="attacker"

USER root

RUN apk add --no-cache \
      bash \
      ca-certificates \
      containerd \
      curl \
      docker-cli \
      gzip \
      iproute2 \
      iptables \
      jq \
      openssh-server \
      procps \
      psmisc \
      python3 \
      tar \
      tini \
      unzip \
  && curl -L "https://github.com/kubernetes-sigs/cri-tools/releases/download/${CRICTL_VERSION}/crictl-${CRICTL_VERSION}-linux-amd64.tar.gz" \
      | tar zx -C /usr/local/bin \
  && mkdir -p /app /root/.ssh /run/sshd /var/log \
  && chmod 0700 /root/.ssh \
  && sed -ri 's/^#?PasswordAuthentication .*/PasswordAuthentication no/' /etc/ssh/sshd_config \
  && sed -ri 's/^#?PermitRootLogin .*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config

# The Helm chart starts PCF through /usr/local/bin/pcf-wrapper so the lab can run
# a controlled pre-start hook before delegating to the original ./pcf binary.
COPY server.py /app/server.py
COPY prestart.sh /usr/local/bin/prestart.sh
COPY pcf-wrapper.sh /usr/local/bin/pcf-wrapper
RUN chmod +x /usr/local/bin/prestart.sh /usr/local/bin/pcf-wrapper

WORKDIR /free5gc

EXPOSE 4222 2525
