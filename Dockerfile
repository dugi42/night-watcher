# syntax=docker/dockerfile:1
FROM python:3.12-slim

# Install system dependencies:
# - ffmpeg: required by OpenCV VideoWriter for mp4v codec
# - v4l-utils: V4L2 camera utilities (optional, useful for debugging)
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        v4l-utils \
    && rm -rf /var/lib/apt/lists/*

# Install uv from the official image (pinned binary path)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Install Python dependencies first (cached unless pyproject.toml/uv.lock changes)
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --no-group client --no-install-project --frozen

# Use the project virtualenv for all subsequent commands
ENV PATH="/app/.venv/bin:${PATH}"
ENV YOLO_CONFIG_DIR="/tmp"
ENV YOLO_MODEL_PATH="/app/models/yolo11n.pt"
ENV ASSETS_DIR="/assets"

# Pre-download YOLO11n weights into the image so the container starts immediately.
# YOLO11n is the current best nano model for ARM/CPU edge devices, replacing YOLOv8n.
RUN mkdir -p /tmp/Ultralytics /app/models \
    && chmod -R 777 /tmp/Ultralytics \
    && uv run python -c "from ultralytics import YOLO; YOLO('yolo11n.pt')" \
    && mv /app/yolo11n.pt /app/models/yolo11n.pt

# Copy application code (after dependency install to maximise layer caching)
COPY . .

# FastAPI service port
EXPOSE 8000

CMD ["uv", "run", "uvicorn", "src.service:app", "--host", "0.0.0.0", "--port", "8000"]
