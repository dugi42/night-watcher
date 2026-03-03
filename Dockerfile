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
ENV YOLO_CONFIG_DIR="/tmp/Ultralytics"
ENV STREAMLIT_BROWSER_GATHER_USAGE_STATS="false"

# Copy the application code
COPY . .

# Make port 8501 available to the world outside this container
EXPOSE 8501

# Run app.py when the container launches
CMD ["uv", "run", "streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501"]
