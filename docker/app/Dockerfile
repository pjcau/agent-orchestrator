FROM python:3.12-slim

WORKDIR /workspace

COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[dev,dashboard]" 2>/dev/null || pip install --no-cache-dir pytest ruff pytest-asyncio fastapi uvicorn[standard] websockets

COPY . .

ENV PYTHONPATH=/workspace/src
