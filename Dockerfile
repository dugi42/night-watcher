# Use an official Python runtime as a parent image
FROM python:3.12-slim

# Install uv
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.cargo/bin:${PATH}"

# Set the working directory in the container
WORKDIR /app

# Copy the dependencies file to the working directory
COPY pyproject.toml .

# Install any needed packages specified in pyproject.toml
RUN uv sync pyproject.toml

# Copy the rest of the application's code
COPY app.py .

# Make port 8501 available to the world outside this container
EXPOSE 8501

# Run app.py when the container launches
CMD ["uv", "run", "streamlit", "run", "app.py"]
