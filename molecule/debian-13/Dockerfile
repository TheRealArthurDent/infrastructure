FROM debian:trixie

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        systemd \
        python3-minimal \
        python3-apt \
        sudo \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

RUN sed -i 's/^#PermitRootLogin yes/PermitRootLogin yes/' /etc/ssh/sshd_config \
    && sed -i 's/^#PubkeyAuthentication yes/PubkeyAuthentication yes/' /etc/ssh/sshd_config

# Create test user with sudo access
RUN useradd -m -s /bin/bash testuser \
    && usermod -aG sudo testuser \
    && echo 'testuser ALL=(ALL) NOPASSWD:ALL' >> /etc/sudoers.d/testuser

RUN systemctl set-default multi-user.target

VOLUME [ "/sys/fs/cgroup" ]
ENTRYPOINT ["/lib/systemd/systemd"]
