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
        nodejs \
        npm \
    && rm -rf /var/lib/apt/lists/*

# pnpm + yarn via corepack (ships with the npm we just installed). Enabling
# corepack makes `pnpm`/`yarn` resolve on first use without a global install.
RUN corepack enable || true

# The launcher passes HOME=/home/u and runs as an arbitrary host uid. Pre-create
# a world-writable home so tools that touch $HOME (npm cache, git) don't fail
# when the uid has no /etc/passwd entry.
RUN mkdir -p /home/u && chmod 0777 /home/u
ENV HOME=/home/u

# Sanity: fail the build if a core tool is missing.
RUN git --version && node --version && python3 --version && rg --version >/dev/null

WORKDIR /work
