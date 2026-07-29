# syntax=docker/dockerfile:1.7

# ============================================================
# Stage 1: Build dependencies
# ============================================================
FROM docker.io/library/python:3.14.6-slim@sha256:cea0e6040540fb2b965b6e7fb5ffa00871e632eef63719f0ea54bca189ce14a6 AS builder

WORKDIR /app

# Install runtime dependencies and patch OS packages for CVEs
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/*

# Copy only the requirements file to leverage Docker layer caching
COPY requirements.txt .

# Install Python dependencies into the user's home directory (--user)
# This avoids polluting the system Python and makes copying easy
RUN pip install --no-cache-dir --user -r requirements.txt

# ============================================================
# Stage 2: Minimal runtime image
# ============================================================
FROM docker.io/library/python:3.14.6-slim@sha256:cea0e6040540fb2b965b6e7fb5ffa00871e632eef63719f0ea54bca189ce14a6 AS runtime

WORKDIR /app

# Create a non‑root user (app:app)
RUN groupadd -r app && useradd -r -g app app

# Copy installed Python packages from the builder stage
COPY --from=builder /root/.local /home/app/.local

# Copy only the production source code (no tests, no shell scripts, no docs)
COPY elt/ ./elt/
COPY train/ ./train/
COPY utils/ ./utils/
COPY main.py .

# Make pip‑installed scripts (like mlflow, onnxruntime tools) available
ENV PATH=/home/app/.local/bin:$PATH
ENV PYTHONUNBUFFERED=1

# All subsequent commands run as the unprivileged user
USER app

# The training job entrypoint – identical to the ACA job command
ENTRYPOINT ["python", "main.py"]