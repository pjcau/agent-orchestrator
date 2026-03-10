FROM python:3.14-slim

WORKDIR /workspace

# Copy source first so editable install works
COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir -e ".[dev,dashboard,anthropic,openai,postgres]"

COPY . .

ENV PYTHONPATH=/workspace/src
