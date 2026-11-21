# MAS-EDS Hardened Sandbox Environment
# Pre-baked image for isolated testing, linting, and vulnerability scanning

FROM python:3.11-slim

# Install system dependencies including git
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install SWE toolchain into system site-packages
RUN pip install --no-cache-dir \
    pytest>=8.1.0 \
    pytest-asyncio>=0.23.0 \
    pytest-cov>=5.0.0 \
    ruff>=0.4.0 \
    bandit>=1.7.8 \
    pip-audit>=2.7.0

# Create unprivileged sandbox user and workspace directory
RUN groupadd -g 1000 sandbox && \
    useradd -u 1000 -g sandbox -s /bin/bash -m sandbox && \
    mkdir -p /workspace && \
    chown -R sandbox:sandbox /workspace /home/sandbox

# Switch to non-root sandbox user
USER sandbox
WORKDIR /workspace

# Environment paths
ENV PATH="/home/sandbox/.local/bin:${PATH}"
ENV PYTHONPATH="/workspace"
ENV PYTHONUNBUFFERED=1

# Default execution command
CMD ["pytest", "-v", "--tb=short"]
