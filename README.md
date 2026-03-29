# 🦉 Night Watcher — Auge der Nacht

A Raspberry Pi wildlife camera that uses YOLO object detection to spot animals
in your garden, record video clips, and present statistics and health metrics
in a web dashboard.

![Night Watcher Logo](assets/logo.jpeg)

---

## Architecture

```text
┌────────────────────────────────────────────┐        ┌──────────────────────────────┐
│  Raspberry Pi  (Docker Compose)            │  HTTP  │  Your laptop / desktop       │
│                                            │◄──────►│                              │
│  ┌─────────────────────────────────────┐   │  :8000 │  streamlit run app.py        │
│  │  night-watcher                      │   │        │                              │
│  │  Camera → YOLO11n → Tracker         │   │        │  📷 Live MJPEG stream        │
│  │               ↓           ↓         │   │        │  📊 Detection statistics      │
│  │          /assets/video   JSON       │   │        │  🩺 Health monitoring         │
│  │          recordings      history    │   │        └──────────────────────────────┘
│  │  FastAPI :8000                      │   │
│  └────────────┬────────────────────────┘   │
│               │ OTLP HTTP                  │
│  ┌────────────▼────────────────────────┐   │
│  │  otel-collector  :4317/:4318        │   │
│  │  Prometheus scrape endpoint :9464   │   │
│  └────────────┬────────────────────────┘   │
│               │ scrape :9464               │
│  ┌────────────▼────────────────────────┐   │
│  │  prometheus  :9090                  │   │
│  └─────────────────────────────────────┘   │
└────────────────────────────────────────────┘
```

The **Pi service** (`src/service.py`) runs inside Docker:

- Opens the webcam and runs YOLO11n inference on every frame.
- Overlays a live timestamp on every frame — visible in the stream.
- Groups detections into *sessions* (one UUID per continuous sighting).
- Records an annotated MP4 video for each session.
- Appends session metadata to `/assets/meta/detections.json`.
- Writes structured logs to `/assets/logs/app.db` (SQLite).
- Ships metrics and traces to the **OpenTelemetry Collector** via OTLP HTTP.
- Exposes an HTTP API on port **8000**.

The **Streamlit client** (`app.py`) runs on your local machine. It requires
no camera or GPU and connects to the Pi over the network.

---

## Raspberry Pi Setup

### 1. Clone the repository

```bash
ssh <your-pi-user>@<your-pi-hostname>
git clone <repo-url> night-watcher
cd night-watcher
```

### 2. Start the stack

```bash
docker compose up --build -d
```

This starts three services:

| Service | Port(s) | Purpose |
| --- | --- | --- |
| `night-watcher` | `8000` | Camera, YOLO detection, HTTP API |
| `otel-collector` | `4317` (gRPC), `4318` (HTTP), `9464` (Prometheus) | Receives OTLP, exposes metrics |
| `prometheus` | `9090` | Scrapes and stores time-series metrics |

On first build the YOLO11n weights are downloaded and baked into the image.

### 3. Check logs

```bash
# Application logs
docker compose logs -f night-watcher

# OTel Collector events
docker compose logs -f otel-collector
```

### 4. Prometheus UI

Open `http://<your-pi-hostname>:9090` to query metrics directly.

### Persistent storage

Recorded videos, detection metadata, and application logs are stored in
`./assets/` on the Pi host and survive container restarts.

```text
assets/
├── logs/
│   └── app.db            ← structured application logs (SQLite)
├── meta/
│   └── detections.json   ← all detection sessions
└── video/
    └── <uuid>.mp4        ← one annotated clip per session
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
# Default — connects to the URL set in DEFAULT_URL (app.py) or RASPI_URL env var
streamlit run app.py

# Custom Pi address
RASPI_URL=http://<your-pi-ip>:8000 streamlit run app.py
```

Open `http://localhost:8501` in your browser.

---

## Dashboard Tabs

### 📷 Live Stream

- Live MJPEG feed with a `HH:MM:SS` timestamp burned into every frame.
- **⛶ Full Screen** button — opens the raw stream in a new browser tab.
- **Last Frame** metric shows the exact capture time and age in milliseconds.
- Detection status: active/paused, detected classes, session ID.

### 📊 Statistics

- Summary metrics: session count, total detection time, unique classes, top class.
- Per-class bar charts: detection count and average duration.
- Sessions-per-day time series.
- Expandable list of the 30 most recent sessions with inline MP4 playback.

### 🩺 Health

Auto-refreshes every 5–10 seconds using Streamlit fragments.

| Section | Data source | Refresh |
| --- | --- | --- |
| System Health | `/health/detailed` | 5 s |
| Docker Services | `/health/docker` | 10 s |
| Application Metrics | `/metrics/app` | 5 s |
| Application Logs | `/logs` | 10 s |

**System Health** shows CPU %, memory, disk usage (progress bars), CPU
temperature with a colour indicator (🟢 < 60 °C · 🟡 < 75 °C · 🔴 ≥ 75 °C),
and system uptime.

**Docker Services** lists all containers visible via the Docker socket with
their state (🟢 running · 🟡 other · 🔴 exited), image, status, and port
bindings.

**Application Metrics** shows frames processed since startup, average FPS,
average frame processing time, session count, and a bar chart of detections
by class.

**Application Logs** shows the most recent log entries from SQLite with
level filtering (DEBUG / INFO / WARNING / ERROR / CRITICAL).

---

## API Reference (Pi service)

| Method | Endpoint | Description |
| --- | --- | --- |
| `GET` | `/health` | Liveness probe |
| `GET` | `/health/detailed` | System health — CPU, memory, disk, temperature, uptime |
| `GET` | `/health/docker` | Docker container list (requires socket mount) |
| `GET` | `/frame` | Latest annotated JPEG frame |
| `GET` | `/stream` | MJPEG video stream |
| `GET` | `/status` | Current detection state + frame age (JSON) |
| `GET` | `/metrics/app` | Runtime metrics snapshot — frames, FPS, detections (JSON) |
| `GET` | `/logs` | Recent log entries from SQLite (JSON) |
| `GET` | `/detections` | Full session history (JSON array) |
| `GET` | `/video/{uuid}` | Download/stream an MP4 recording |
| `GET` | `/detection/config` | Current enable/schedule configuration |
| `POST` | `/detection/config` | Update enable/schedule/threshold configuration |

### `/logs` query parameters

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `limit` | int | `200` | Max entries to return (1–1000) |
| `level` | string | — | Filter to a level: `DEBUG` · `INFO` · `WARNING` · `ERROR` · `CRITICAL` |
| `since` | float | — | UNIX timestamp — only return entries newer than this |

---

## Observability Stack

### OpenTelemetry

The app uses the **OpenTelemetry SDK** (`opentelemetry-sdk`) to emit:

| Signal | Instruments |
| --- | --- |
| Metrics | `night_watcher.frames.processed` (counter), `night_watcher.frames.processing_ms` (histogram), `night_watcher.detections.total` (counter, labelled by class), `night_watcher.sessions.started` (counter) |
| Traces | Span exporter to OTel Collector (for future instrumentation) |

Metrics are exported every 15 s via **OTLP HTTP** to the `otel-collector`
service, which exposes them on a Prometheus scrape endpoint (`:9464`).

If the OTel Collector is unreachable at startup, the SDK silently falls back
to a no-op provider — the application keeps running.

### Prometheus

Prometheus scrapes the OTel Collector every 15 s and retains data for 30
days.  Access the UI at `http://<your-pi-hostname>:9090`.

Example queries:

```promql
# Current average FPS
rate(night_watcher_frames_processed_total[1m])

# Frame processing latency p95
histogram_quantile(0.95, rate(night_watcher_frames_processing_ms_bucket[5m]))

# Detections by class
night_watcher_detections_total
```

### Structured Logs (SQLite)

All `night_watcher.*` logger output is captured by `SQLiteLogHandler` and
written to `/assets/logs/app.db`.  The `/logs` endpoint lets the Streamlit
dashboard query recent entries without SSH access to the Pi.

---

## Configuration

### Environment variables

| Variable | Default | Description |
| --- | --- | --- |
| `CAMERA_DEVICE` | `/dev/video0` | V4L2 camera device |
| `YOLO_MODEL_PATH` | `/app/models/yolo11n.pt` | YOLO model weights path |
| `ASSETS_DIR` | `/assets` | Root for video, metadata, and logs |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://otel-collector:4318` | OTel Collector HTTP endpoint |
| `RASPI_URL` | `http://<your-pi-hostname>:8000` | Pi service URL (client-side only) |

### Detection config (live, via dashboard or API)

| Parameter | Default | Description |
| --- | --- | --- |
| `enabled` | `true` | Master detection switch |
| `schedule_enabled` | `false` | Restrict detection to a time window |
| `schedule_start` | `20:00` | Start of active window (`HH:MM`) |
| `schedule_end` | `06:00` | End of active window (`HH:MM`, supports overnight) |
| `conf_threshold` | `0.35` | Minimum YOLO confidence score (0–1) |

---

## Detected Classes

Night Watcher reports **people** and **animals** from the COCO dataset:

`person` · `bird` · `cat` · `dog` · `horse` · `sheep` · `cow` · `elephant` · `bear` · `zebra` · `giraffe`
