FROM python:3.12-slim

ARG CRICTL_VERSION="v1.30.0"

# Install sshd + tini for a clean PID1
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
 openssh-server tini curl containerd unzip jq \
 && rm -rf /var/lib/apt/lists/*

# Basic SSH hardening (pubkey only; you can adjust as needed)
RUN sed -ri 's/^#?PasswordAuthentication .*/PasswordAuthentication no/' /etc/ssh/sshd_config \
 && sed -ri 's/^#?PermitRootLogin .*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config \
 && mkdir -p /root/.ssh

RUN curl -L https://github.com/kubernetes-sigs/cri-tools/releases/download/${CRICTL_VERSION}/crictl-${CRICTL_VERSION}-linux-amd64.tar.gz \
    | tar zx -C /usr/local/bin

# App
WORKDIR /app
COPY server.py /app/server.py
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Expose SSH and the TCP server (SMTP-like) on port 25
EXPOSE 22 25

# Use tini as entrypoint and our script as CMD
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/usr/local/bin/entrypoint.sh"]
