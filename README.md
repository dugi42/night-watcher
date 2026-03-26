# 🦉 Night Watcher — Auge der Nacht

A Raspberry Pi wildlife camera that uses YOLO object detection to spot animals
in your garden, record video clips, and present statistics in a web dashboard.

![Night Watcher Logo](assets/logo.jpeg)

---

## Architecture

```text
┌─────────────────────────────────┐        ┌──────────────────────────────┐
│  Raspberry Pi  (Docker)         │  HTTP  │  Your laptop / desktop       │
│                                 │◄──────►│                              │
│  Camera → YOLO → Tracker        │  :8000 │  streamlit run app.py        │
│              ↓          ↓       │        │                              │
│         /assets/video   JSON    │        │  Live MJPEG stream           │
│         recordings      history │        │  Detection statistics        │
└─────────────────────────────────┘        └──────────────────────────────┘
```

The **Pi service** (`src/service.py`) runs entirely inside Docker:

- Opens the webcam and runs YOLOv8n inference on every frame.
- Groups detections into *sessions* (one UUID per continuous sighting).
- Records an MP4 video for each session.
- Appends session metadata to `/assets/meta/detections.json`.
- Exposes an HTTP API on port **8000**.

The **Streamlit client** (`app.py`) runs on your local machine and talks to
the Pi over the network. It requires no camera or GPU.

---

## Raspberry Pi Setup

### 1. Clone the repository

```bash
ssh pi51@raspi.local
git clone <repo-url> night-watcher
cd night-watcher
```

### 2. Start the service

```bash
docker compose up --build -d
```

The container will:

1. Download YOLOv8n weights (only on first build).
2. Open the camera and start detection immediately.
3. Serve the API at `http://raspi.local:8000`.

### 3. Check logs

```bash
docker compose logs -f
```

### Persistent storage

Recorded videos and detection metadata are stored in `./assets/` on the Pi
host (mounted into the container). They survive container restarts.

```text
assets/
├── meta/
│   └── detections.json   ← all detection sessions
└── video/
    └── <uuid>.mp4        ← one clip per session
```

---

## Client Setup (your laptop)

### Install client dependencies

```bash
# With uv (recommended)
uv sync --group client

# Or with pip
pip install streamlit requests pandas
```

### Run the dashboard

```bash
# Default — connects to http://raspi.local:8000
streamlit run app.py

# Custom Pi address
RASPI_URL=http://192.168.1.42:8000 streamlit run app.py
```

Open `http://localhost:8501` in your browser.

---

## API Reference (Pi service)

| Method | Endpoint | Description |
| ------ | -------- | ----------- |
| `GET` | `/health` | Liveness probe |
| `GET` | `/frame` | Latest annotated JPEG frame |
| `GET` | `/stream` | MJPEG video stream |
| `GET` | `/status` | Current detection state (JSON) |
| `GET` | `/detections` | Full session history (JSON array) |
| `GET` | `/video/{uuid}` | Download/stream an MP4 recording |

---

## Configuration

| Variable | Default | Where |
| -------- | ------- | ----- |
| `CAMERA_DEVICE` | `/dev/video0` | Pi container |
| `YOLO_MODEL_PATH` | `/app/models/yolov8n.pt` | Pi container |
| `ASSETS_DIR` | `/assets` | Pi container |
| `RASPI_URL` | `http://raspi.local:8000` | Client machine |

---

## Detected Classes

Night Watcher reports **people** and **animals** from the COCO dataset:

`person` · `bird` · `cat` · `dog` · `horse` · `sheep` · `cow` · `elephant` · `bear` · `zebra` · `giraffe`
