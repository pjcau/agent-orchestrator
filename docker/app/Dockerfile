FROM python:3.12-slim

WORKDIR /workspace

COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[dev,dashboard,anthropic,openai]" 2>/dev/null || pip install --no-cache-dir pytest ruff pytest-asyncio fastapi "uvicorn[standard]" websockets anthropic openai httpx

COPY . .

ENV PYTHONPATH=/workspace/src
