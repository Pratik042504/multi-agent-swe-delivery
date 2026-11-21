# Production Dockerfile for MAS-EDS Backend
# Compatible with Render, Cloud Run, Fly.io, and Docker Swarm

FROM python:3.11-slim

# Install system dependencies including Git
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code and frontend assets
COPY src/ ./src/
COPY pyproject.toml .
COPY README.md .

# Environment settings
ENV PYTHONUNBUFFERED=1
ENV HOST=0.0.0.0
ENV PORT=8080
ENV SANDBOX_MODE=local

EXPOSE 8080

# Health check probe
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:${PORT:-8080}/health || exit 1

CMD ["python", "-m", "src.server"]
