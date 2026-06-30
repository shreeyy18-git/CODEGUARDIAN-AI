# ── CodeGuardian AI — Docker image ─────────────────────────────────────
# Build:  docker build -t codeguardian-ai ./codeguardian-ai
# Run:    docker run --env-file .env -p 8000:8000 codeguardian-ai
# -----------------------------------------------------------------------

FROM python:3.11-slim AS base

# System deps required by semgrep / bandit / ruff
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git \
        curl \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .

# Ensure the reports directory exists for generated review artifacts
RUN mkdir -p /app/reports

# Expose the FastAPI port
EXPOSE 8000

# Health check hits the /health endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run the ASGI server
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
