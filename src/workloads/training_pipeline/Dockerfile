# syntax=docker/dockerfile:1.7

# Keep the exact same Python minor version in both stages to avoid wheel/ABI drift.
ARG PYTHON_IMAGE=docker.io/library/python:3.14.6-slim-bookworm@sha256:86f975aca15cf04a40b399eebede9aea7c82eae084d1f1a0a6ef6bcaae871a30

# ============================================================
# Stage 1: Build dependencies into an isolated virtualenv
# ============================================================
FROM ${PYTHON_IMAGE} AS builder

# Install system dependencies required by LightGBM
RUN apt-get update && \
    apt-get install -y --no-install-recommends libgomp1 && \
    rm -rf /var/lib/apt/lists/*
    
WORKDIR /app

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Copy only dependency metadata first so Docker can reuse this layer when app code changes.
COPY requirements.txt ./

# Build a virtualenv that will be copied into the runtime image.
# The cache mount speeds up repeat builds without polluting the final image.
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m venv /opt/venv && \
    /opt/venv/bin/python -m pip install --upgrade pip setuptools wheel && \
    /opt/venv/bin/python -m pip install --no-cache-dir -r requirements.txt

# ============================================================
# Stage 2: Minimal runtime image
# ============================================================
FROM ${PYTHON_IMAGE} AS runtime

WORKDIR /app

ENV VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    ORT_DISABLE_WARNINGS=1
    
# Create an unprivileged runtime account.
RUN groupadd --system app && \
    useradd --system --gid app --create-home --home-dir /home/app app

# Copy only the prepared virtualenv and the production source tree.
COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app elt/ ./elt/
COPY --chown=app:app train/ ./train/
COPY --chown=app:app utils/ ./utils/
COPY --chown=app:app main.py ./

USER app

ENTRYPOINT ["python", "main.py"]