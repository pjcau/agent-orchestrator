# Batteries-included jail image for `ago … --client-tools`.
#
# The default jail base (ubuntu:24.04) is bare, so the agent hits
# `shell_spawn_failed` the moment it reaches for git / node / python / etc.
# This image ships the common toolchain so a real task (clone, build, test,
# commit) runs end-to-end inside the sandbox. Point a project at it with
# `jail_image:` in .ago.yaml (or AGO_JAIL_IMAGE).
#
# Build (OrbStack):
#   docker build -t ago-jail:latest docker/ago-jail
#
# Notes:
# - The launcher runs the container as the host uid:gid (--user), mounts the
#   real `ago` binary at /usr/local/bin/ago, and bind-mounts ONLY the project
#   dir at /work. So this image needs the TOOLS, not the binary.
# - git is present, but `git push` still needs credentials, which the jail does
#   not mount. Commit inside the jail (the commit lands in the real repo via the
#   /work bind-mount), then push from the host.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    NODE_MAJOR=20

# Core CLI toolchain the agent reaches for most. Kept to apt where possible so
# the image stays reproducible and arch-independent (amd64/arm64).
#
# Network/process/DB tools (iproute2 → ss, lsof, procps → ps, net-tools →
# netstat, postgresql-client → psql/pg_isready, sqlite3) were added because a
# devops agent driving a docker-compose app reaches for them to probe ports and
# the database; without them every probe wasted a step on `shell_spawn_failed`.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        wget \
        git \
        openssh-client \
        jq \
        unzip \
        zip \
        ripgrep \
        less \
        vim-tiny \
        nano \
        make \
        build-essential \
        python3 \
        python3-pip \
        python3-venv \
        python-is-python3 \
        iproute2 \
        lsof \
        procps \
        net-tools \
        iputils-ping \
        dnsutils \
        netcat-openbsd \
        postgresql-client \
        sqlite3 \
        tree \
    && rm -rf /var/lib/apt/lists/*

# Node via NodeSource: Ubuntu's apt node is 18, but current pnpm/yarn need
# Node >= 22. A dev jail should ship a modern Node anyway.
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# pnpm + yarn as real global binaries (Node 22 from NodeSource is new enough).
# Installed directly via npm — no corepack shims that would re-download the
# package manager on first use at runtime. Non-`|| true` so a failure breaks
# the build instead of shipping a half-equipped image.
RUN npm install -g pnpm yarn

# Docker CLI + Compose/Buildx plugins (official Docker apt repo). Many projects
# are entirely `docker compose …` driven (build, up, logs, down), so a devops
# agent hits `docker` immediately. This ships only the CLIENT, not a daemon.
#
# SECURITY: the jail launcher does NOT mount the host Docker socket, so `docker`
# here cannot reach a daemon — `docker compose config`/`build` context checks
# work, but `up`/`ps` fail with "cannot connect to the daemon" until a socket is
# explicitly bind-mounted. That is deliberate: mounting /var/run/docker.sock is
# root-equivalent on the host and would defeat the jail. Run the live stack from
# the host, or opt in to a socket mount knowingly. See managing-local-projects.md.
RUN install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc \
    && chmod a+r /etc/apt/keyrings/docker.asc \
    && . /etc/os-release \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
        > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        docker-ce-cli \
        docker-buildx-plugin \
        docker-compose-plugin \
    && rm -rf /var/lib/apt/lists/*

# `sudo` shim. The jail runs as an arbitrary host uid with no real root, so the
# real sudo (setuid + sudoers + a passwd entry) cannot work here — yet agents
# reach for `sudo apt-get …` out of habit, wasting a step on `shell_spawn_failed`.
# Inside the container the mapped user already owns /work and $HOME, so drop the
# `sudo` prefix and exec the rest. /usr/local/bin precedes /usr/bin on PATH, so
# this wins even if a real sudo is ever pulled in transitively.
RUN printf '#!/bin/sh\nexec "$@"\n' > /usr/local/bin/sudo \
    && chmod 0755 /usr/local/bin/sudo

# The launcher passes HOME=/home/u and runs as an arbitrary host uid. Pre-create
# a world-writable home so tools that touch $HOME (npm cache, git) don't fail
# when the uid has no /etc/passwd entry.
RUN mkdir -p /home/u && chmod 0777 /home/u
ENV HOME=/home/u

# Sanity: fail the build if a core tool is missing (incl. pnpm — it silently
# went missing once when corepack enable no-op'd, see git history). The network/
# DB/docker probes are `command -v` (no daemon needed) so the check passes in a
# plain `docker build` with no socket.
RUN git --version && node --version && python3 --version \
    && python --version && rg --version >/dev/null \
    && pnpm --version && yarn --version \
    && command -v ss && command -v lsof && command -v ps \
    && command -v psql && command -v sqlite3 \
    && docker --version && docker compose version \
    && [ "$(sudo id -u 2>/dev/null || echo fail)" != fail ]

WORKDIR /work
