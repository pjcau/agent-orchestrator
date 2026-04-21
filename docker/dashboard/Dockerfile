# ============================================================
# Stage 1: Build React frontend
# ============================================================
FROM node:25-alpine AS frontend-build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --ignore-scripts 2>/dev/null || npm install --ignore-scripts
COPY frontend/ .
RUN npm run build

# ============================================================
# Stage 2: Build Rust extension (optional — graceful failure)
# ============================================================
FROM python:3.14-slim AS rust-build
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl build-essential pkg-config && \
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y && \
    rm -rf /var/lib/apt/lists/*
ENV PATH="/root/.cargo/bin:${PATH}"
RUN pip install --no-cache-dir maturin
WORKDIR /rust
COPY rust/ .
RUN maturin build --release --out dist 2>/dev/null || echo "Rust build skipped"

# ============================================================
# Stage 3: Final Python image
# ============================================================
FROM python:3.14-slim

WORKDIR /workspace

# Install Docker CLI for sandbox container management (optional — sandbox
# features degrade gracefully if Docker daemon is not accessible).
RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates curl gnupg && \
    install -m 0755 -d /etc/apt/keyrings && \
    curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc && \
    chmod a+r /etc/apt/keyrings/docker.asc && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
      https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
      > /etc/apt/sources.list.d/docker.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends docker-ce-cli && \
    apt-get purge -y gnupg && \
    rm -rf /var/lib/apt/lists/*

# Copy source first so editable install works
COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir -e ".[dev,dashboard,anthropic,openai,postgres]"

# Install Rust extension if built successfully (graceful: no-op if missing)
COPY --from=rust-build /rust/dist/*.whl /tmp/wheels/
RUN pip install /tmp/wheels/*.whl 2>/dev/null || true && rm -rf /tmp/wheels

COPY . .

# Copy built React assets
COPY --from=frontend-build /app/dist /workspace/frontend/dist

ENV PYTHONPATH=/workspace/src
