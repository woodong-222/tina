# Build stage
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

WORKDIR /app

# Install dependencies
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# Runtime stage
FROM python:3.12-slim

WORKDIR /app

# Copy the environment from the builder
COPY --from=builder /app/.venv /app/.venv

# Copy the application code
COPY . .

# Set environment variables
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

# Run the bot
CMD ["python", "bot.py"]
