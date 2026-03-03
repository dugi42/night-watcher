# Use an official Python runtime as a parent image
FROM python:3.12-slim

# Install uv from Astral's published image (deterministic binary path)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Set the working directory in the container
WORKDIR /app

# Copy project metadata and install dependencies only
COPY pyproject.toml ./
RUN uv sync --no-dev --no-install-project

# Use the project virtualenv for runtime commands
ENV PATH="/app/.venv/bin:${PATH}"
ENV YOLO_CONFIG_DIR="/tmp"
ENV YOLO_MODEL_PATH="/app/models/yolov8n.pt"
ENV STREAMLIT_BROWSER_GATHER_USAGE_STATS="false"

# Pre-create writable config/model dirs and pre-download YOLO weights
RUN mkdir -p /tmp/Ultralytics /app/models \
    && chmod -R 777 /tmp/Ultralytics \
    && uv run python -c "from ultralytics import YOLO; YOLO('yolov8n.pt')" \
    && mv /app/yolov8n.pt /app/models/yolov8n.pt

# Copy the application code
COPY . .

# Make port 8501 available to the world outside this container
EXPOSE 8501

# Run app.py when the container launches
CMD ["uv", "run", "streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501"]
