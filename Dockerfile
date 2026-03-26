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

# Install Python dependencies first (cached unless pyproject.toml changes)
COPY pyproject.toml ./
RUN uv sync --no-dev --no-group client --no-install-project

# Use the project virtualenv for all subsequent commands
ENV PATH="/app/.venv/bin:${PATH}"
ENV YOLO_CONFIG_DIR="/tmp"
ENV YOLO_MODEL_PATH="/app/models/yolov8n.pt"
ENV ASSETS_DIR="/assets"

# Pre-download YOLO weights into the image so the container starts immediately
RUN mkdir -p /tmp/Ultralytics /app/models \
    && chmod -R 777 /tmp/Ultralytics \
    && uv run python -c "from ultralytics import YOLO; YOLO('yolov8n.pt')" \
    && mv /app/yolov8n.pt /app/models/yolov8n.pt

# Copy application code (after dependency install to maximise layer caching)
COPY . .

# FastAPI service port
EXPOSE 8000

CMD ["uv", "run", "uvicorn", "src.service:app", "--host", "0.0.0.0", "--port", "8000"]
