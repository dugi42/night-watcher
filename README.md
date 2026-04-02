# 🦉 Night Watcher — Auge der Nacht

[![Release](https://img.shields.io/github/v/tag/dugi42/night-watcher?label=release&sort=semver)](https://github.com/dugi42/night-watcher/releases)
[![CI](https://img.shields.io/github/actions/workflow/status/dugi42/night-watcher/ci.yml?branch=main&label=CI)](https://github.com/dugi42/night-watcher/actions/workflows/ci.yml)
[![Coverage](https://codecov.io/gh/dugi42/night-watcher/graph/badge.svg)](https://codecov.io/gh/dugi42/night-watcher)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Citation](https://img.shields.io/badge/citation-CFF-blue)](CITATION.cff)
[![Last Commit](https://img.shields.io/github/last-commit/dugi42/night-watcher)](https://github.com/dugi42/night-watcher/commits/main)
[![Python](https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Streamlit](https://img.shields.io/badge/Streamlit-dashboard-FF4B4B?logo=streamlit&logoColor=white)](https://streamlit.io/)
[![FastAPI](https://img.shields.io/badge/FastAPI-API-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Docker Compose](https://img.shields.io/badge/Docker%20Compose-deployment-2496ED?logo=docker&logoColor=white)](https://docs.docker.com/compose/)
[![Prometheus](https://img.shields.io/badge/Prometheus-metrics-E6522C?logo=prometheus&logoColor=white)](https://prometheus.io/)
[![Raspberry Pi](https://img.shields.io/badge/Raspberry%20Pi-5-A22846?logo=raspberrypi&logoColor=white)](https://www.raspberrypi.com/products/raspberry-pi-5/)

A Raspberry Pi wildlife camera that uses YOLO object detection to spot animals
in your garden, record video clips, and present statistics and health metrics
in a web dashboard.

![Night Watcher Logo](assets/logo.jpeg)

---

## Stable Release 1.2.1

Night Watcher `1.2.1` completes the Pi-side stack — everything now runs on
the Raspberry Pi, no local client required:

- Real-time YOLO11n detection for people and animals.
- Session-based annotated video recording with persistent metadata storage.
- **Streamlit dashboard runs as a Docker service on the Pi** (port 8501) — open
  it in any browser on your LAN, no local installation needed.
- **Grafana** instance with Prometheus as the pre-configured datasource — all
  metrics are available as time-series dashboards out of the box (port 3000).
- Every resource metric ships both a percentage *and* an absolute value
  (e.g. `disk_used_gb` + `disk_free_gb` + `disk_total_gb`).
- YOLO inference time is measured inside the background worker thread so
  `avg_processing_ms` reflects actual model latency, not camera I/O.
- OpenTelemetry and Prometheus observability for runtime, thermal, and power metrics.
- A standalone `metrics-exporter` service that records hardware and app metrics
  independently of the dashboard — metrics persist across restarts.
- Docker Compose deployment for repeatable, self-contained operation.

---

## Quality Status

- GitHub Actions runs unit tests and publishes a coverage report on every push and pull request.
- Coverage is reported via Codecov once the workflow has executed on GitHub.
- The CI badge becomes live after the workflow file is pushed and GitHub has completed the first run on `main`.

---

## Architecture

```text
┌──────────────────────────────────────────────────────────────────────────┐
│  Raspberry Pi (Docker host)                                               │
│                                                                           │
│  ┌──────────────────────────────────────────┐                            │
│  │  night-watcher  :8000  (FastAPI)          │                            │
│  │                                           │                            │
│  │  ┌──────────┐  ┌──────────┐              │                            │
│  │  │ camera   │→ │ detector │              │                            │
│  │  │ (V4L2)   │  │ (YOLO11n)│              │                            │
│  │  └──────────┘  └────┬─────┘              │                            │
│  │                     │ detections          │                            │
│  │               ┌─────▼──────┐             │                            │
│  │               │  tracker   │→ callbacks  │                            │
│  │               └────────────┘      │      │                            │
│  │  /assets/video/*.mp4    ┌─────────▼──┐  │                            │
│  │  /assets/meta/          │  recorder  │  │                            │
│  │    detections.json      └────────────┘  │                            │
│  │  /assets/logs/app.db                    │                            │
│  │                          ┌───────────┐  │                            │
│  │  Emits OTLP traces  ───→ │ telemetry │  │                            │
│  │                          └───────────┘  │                            │
│  └──────────────────────────────────────────┘                            │
│            │ HTTP /metrics/app                                             │
│            │ shared SQLite (volume)                                        │
│            ▼                                                               │
│  ┌───────────────────────────┐                                            │
│  │  metrics-exporter  :9100  │                                            │
│  │  polls every 15s:         │                                            │
│  │  • psutil (CPU/mem/disk)  │  → absolute + relative per metric          │
│  │  • vcgencmd (PMIC rails)  │                                            │
│  │  • /metrics/app  (HTTP)   │                                            │
│  │  • SQLite log counts      │                                            │
│  └───────────────────────────┘                                            │
│                                                                           │
│  ┌───────────────────────────┐   ┌─────────────────────────────┐         │
│  │  otel-collector           │   │  prometheus  :9090           │         │
│  │  :4317 gRPC               │   │                              │         │
│  │  :4318 HTTP               │   │  scrapes:                    │         │
│  │  :9464 /metrics           │◄──│  • metrics-exporter:9100     │         │
│  │                           │   │  • otel-collector:9464       │         │
│  └───────────────────────────┘   │  retention: 30d              │         │
│                                  └──────────────┬──────────────┘         │
│                                                 │                         │
│  ┌──────────────────────────────┐   ┌──────────▼──────────────┐         │
│  │  dashboard  :8501            │   │  grafana  :3000          │         │
│  │  (Streamlit)                 │   │                          │         │
│  │                              │   │  datasource: Prometheus  │         │
│  │  • queries night-watcher API │   │  (auto-provisioned)      │         │
│  │  • serves MJPEG / video via  │   └──────────────────────────┘         │
│  │    raspberrypi.local hostname│                                         │
│  │  • links to Grafana :3000    │                                         │
│  └──────────────────────────────┘                                         │
└──────────────────────────────────────────────────────────────────────────┘
                    ▲                              ▲
                    │ HTTP :8501 (LAN)             │ HTTP :3000 (LAN)
                    │                              │
              ┌─────┴──────────────────────────────┴──┐
              │         Browser (any device on LAN)    │
              └────────────────────────────────────────┘
```

The **Pi service** (`src/service.py`) runs inside Docker:

- Opens the webcam and runs YOLO11n inference on every frame.
- Overlays a live timestamp on every frame — visible in the stream.
- Groups detections into *sessions* (one UUID per continuous sighting).
- Records an annotated MP4 video for each session.
- Appends session metadata to `/assets/meta/detections.json`.
- Writes structured logs to `/assets/logs/app.db` (SQLite).
- Ships detection metrics to the **OpenTelemetry Collector** via OTLP HTTP.
- Exposes an HTTP API on port **8000**.

The **metrics-exporter** (`src/exporter.py`) runs as a separate Docker service.
It collects all hardware and application metrics independently of the Streamlit dashboard,
so time-series data (including CPU temperature) persists across dashboard restarts:

- Reads hardware metrics directly: CPU, memory, disk, temperature, CPU frequency, uptime.
- Reads PMIC voltage/current/power via `vcgencmd pmic_read_adc` (Raspberry Pi 5).
- Polls the FastAPI service every 15 s for app counters (frames, FPS, detections, sessions).
- Queries the SQLite log database for log counts by level and recent error rate.
- Exposes a standard Prometheus `/metrics` endpoint on port **9100**.

The **Streamlit dashboard** (`app.py`) runs as a Docker service on the Pi
(port 8501). It connects to the other services via Docker-internal hostnames
for server-side API calls; the MJPEG stream and video playback URLs use the
Pi's mDNS hostname (`raspberrypi.local`) so they are reachable directly from
the user's browser without any proxying.

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

This starts six services:

| Service | Port(s) | Purpose |
| --- | --- | --- |
| `night-watcher` | `8000` | Camera, YOLO detection, HTTP API |
| `metrics-exporter` | `9100` | Standalone Prometheus exporter — hardware metrics, app counters, log stats |
| `otel-collector` | `4317` (gRPC), `4318` (HTTP), `9464` (Prometheus scrape) | Receives OTLP, exposes metrics |
| `prometheus` | `9090` | Scrapes and stores time-series metrics (30-day retention) |
| `grafana` | `3000` | Dashboards — Prometheus pre-configured as default datasource |
| `dashboard` | `8501` | Streamlit dashboard — live stream, statistics, health KPIs |

On first build the YOLO11n weights are downloaded and baked into the image.

### 3. Open the dashboard

```text
http://<your-pi-hostname>:8501
```

No local installation required — the dashboard runs on the Pi and is accessible
from any browser on the same LAN.

### 4. Check logs

```bash
# Application logs
docker compose logs -f night-watcher

# Dashboard logs
docker compose logs -f dashboard
```

### 5. Prometheus UI

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

## Dashboard Tabs

### 📷 Live Stream

- Live MJPEG feed with a `HH:MM:SS` timestamp burned into every frame.
- **⛶ Full Screen** button — opens the raw stream in a new browser tab.
- Detection status: active/paused, detected classes, session ID.

### 📊 Statistics

- Summary metrics: session count, total detection time, unique classes, top class.
- Per-class bar charts: detection count and average duration.
- Sessions-per-day time series.
- Expandable list of the 30 most recent sessions with inline MP4 playback.

### 🩺 Health

Auto-refreshes every 5–10 seconds using Streamlit fragments. Displays live
numeric KPIs for each subsystem. For historical time-series analysis, a
prominent link at the top of the tab opens **Grafana** (`:3000`) where all
Prometheus metrics are available as dashboards.

| Section | Data source | Refresh |
| --- | --- | --- |
| Power & Throttle Status | `/health/power` | 10 s |
| Voltage & Current (PMIC) | `/health/pmic` | 5 s |
| System Health | `/health/detailed` | 5 s |
| Docker Services | `/health/docker` | 10 s |
| Application Metrics | `/metrics/app` | 5 s |
| Application Logs | `/logs` | 10 s |

**Power & Throttle Status** shows current throttling flags (under-voltage,
frequency-capping, thermal soft limit) read via `vcgencmd`.

**Voltage & Current (PMIC)** shows live USB-C input voltage, total system
power, and CPU core voltage / current from the MXL7704 PMIC ADC channels,
plus a per-rail breakdown table.

**System Health** shows CPU %, memory (used / total MB), disk (used / free / total GB),
CPU temperature with a colour indicator (🟢 < 60 °C · 🟡 < 75 °C · 🔴 ≥ 75 °C),
and system uptime.

**Docker Services** lists all containers visible via the Docker socket with
their state (🟢 running · 🟡 other · 🔴 exited), image, status, and port
bindings.

**Application Metrics** shows frames processed since startup, average FPS,
average YOLO inference time (ms), session count, and a bar chart of detections
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
| `GET` | `/health/power` | Pi throttle/under-voltage status via `vcgencmd` |
| `GET` | `/health/pmic` | Live voltage & current for all PMIC rails (Pi 5) |
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

### Standalone metrics exporter

`src/exporter.py` runs as a dedicated Docker service and is the primary source
of hardware and application metrics in Prometheus. It reads data directly —
no OTel SDK in the path — so metrics are always present and never depend on
the Streamlit dashboard being open.

| Category | Prometheus metric | Description |
| --- | --- | --- |
| **Hardware** | `night_watcher_hw_cpu_percent` | CPU utilization (%) |
| | `night_watcher_hw_memory_percent` | RAM utilization (%) |
| | `night_watcher_hw_memory_used_mb` | RAM used (MB) |
| | `night_watcher_hw_memory_total_mb` | Total RAM installed (MB) |
| | `night_watcher_hw_memory_available_mb` | Available RAM (MB) |
| | `night_watcher_hw_disk_percent` | Disk utilization (%) on `/assets` |
| | `night_watcher_hw_disk_used_gb` | Disk used (GB) on `/assets` |
| | `night_watcher_hw_disk_total_gb` | Total disk capacity (GB) on `/assets` |
| | `night_watcher_hw_disk_free_gb` | Free disk space (GB) on `/assets` |
| | `night_watcher_hw_temperature_c` | CPU temperature (°C) |
| | `night_watcher_hw_cpu_freq_mhz` | CPU clock frequency (MHz) |
| | `night_watcher_hw_uptime_seconds` | System uptime (s) |
| **PMIC** | `night_watcher_pmic_ext5v_v` | USB-C supply input voltage (V) |
| | `night_watcher_pmic_total_power_w` | Total system power (W) |
| | `night_watcher_pmic_under_voltage` | Under-voltage flag (1 = below 4.75 V) |
| | `night_watcher_pmic_rail_voltage_v{rail}` | Per-rail voltage (V) |
| | `night_watcher_pmic_rail_current_a{rail}` | Per-rail current (A) |
| **App** | `night_watcher_app_service_up` | FastAPI reachability (1 = up) |
| | `night_watcher_app_frames_total` | Frames processed since service start |
| | `night_watcher_app_fps_avg` | Average FPS over service uptime |
| | `night_watcher_app_avg_processing_ms` | Average YOLO inference latency (ms) |
| | `night_watcher_app_sessions_total` | Detection sessions started |
| | `night_watcher_app_detections_total{class_name}` | Detections by YOLO class |
| **Logs** | `night_watcher_log_count_total{level}` | Total log entries by level |
| | `night_watcher_log_errors_last_5m` | ERROR/CRITICAL entries in last 5 min |

### OpenTelemetry

The FastAPI service also emits detection pipeline metrics via the **OpenTelemetry SDK**:

| Signal | Instruments |
| --- | --- |
| **Detection metrics** | `night_watcher.frames.processed` (counter), `night_watcher.frames.processing_ms` (histogram), `night_watcher.detections.total` (counter by class), `night_watcher.sessions.started` (counter) |
| Traces | Span exporter to OTel Collector (for future instrumentation) |

Metrics flow via **OTLP HTTP** → `otel-collector` → Prometheus scrape endpoint (`:9464`).
If the OTel Collector is unreachable at startup the SDK silently falls back to a no-op
provider — the application keeps running.

### Grafana

Grafana runs on port **3000** and is the recommended tool for time-series
dashboards. Prometheus is automatically configured as the default datasource
on startup via `grafana/provisioning/datasources/prometheus.yml` — no
manual setup required.

Access the dashboard at `http://<your-pi-hostname>:3000`.
Default credentials: `admin` / `nightwatcher`.
Anonymous editor access is enabled so you can build dashboards immediately and
the Streamlit dashboard can still link directly to panels.

### Prometheus

Prometheus scrapes both the `metrics-exporter` (`:9100`) and the OTel Collector
(`:9464`) every 15 s, and retains data for **30 days**. Access the raw query UI
at `http://<your-pi-hostname>:9090`.

Example queries:

```promql
# CPU temperature over the last hour
night_watcher_hw_temperature_c

# Input voltage — detect brown-outs
min_over_time(night_watcher_pmic_ext5v_v[1h])

# Total system power
night_watcher_pmic_total_power_w

# App service availability
night_watcher_app_service_up

# Error spike in the last 5 minutes
night_watcher_log_errors_last_5m
```

### Structured Logs (SQLite)

All `night_watcher.*` logger output is captured by `SQLiteLogHandler` and
written to `/assets/logs/app.db`.  The `/logs` endpoint lets the Streamlit
dashboard query recent entries without SSH access to the Pi.

---

## Long-term Health Analysis

After collecting data for a few days, run the analysis script on your laptop
to generate a health report in [`docs/RUN_REPORTS.md`](docs/RUN_REPORTS.md):

```bash
uv run scripts/analyze_health.py \
    --prometheus http://<your-pi-hostname>:9090 \
    --days 7

# Preview without writing
uv run scripts/analyze_health.py --no-write
```

The script queries Prometheus, computes statistics (min / avg / max / P95),
detects notable events (under-voltage, high temperature, CPU / RAM spikes),
and generates Mermaid time-series charts for temperature, CPU load, power,
and input voltage.  Results are injected into `docs/RUN_REPORTS.md`.

---

## Configuration

### Environment variables

| Variable | Default | Description |
| --- | --- | --- |
| `CAMERA_DEVICE` | `/dev/video0` | V4L2 camera device |
| `YOLO_MODEL_PATH` | `/app/models/yolo11n.pt` | YOLO model weights path |
| `ASSETS_DIR` | `/assets` | Root for video, metadata, and logs |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://otel-collector:4318` | OTel Collector HTTP endpoint |
| `RASPI_URL` | `http://raspberrypi.local:8000` | FastAPI service URL — must use the Pi's public hostname so the MJPEG stream and video embeds work in the browser (`dashboard` service) |
| `PROMETHEUS_URL` | `http://prometheus:9090` | Prometheus URL — Docker-internal hostname used for server-side API calls (`dashboard` service) |
| `GRAFANA_URL` | `http://raspberrypi.local:3000` | Grafana URL linked from the Health tab — must use the Pi's public hostname so the link opens in the browser (`dashboard` service) |
| `NIGHT_WATCHER_URL` | `http://night-watcher:8000` | FastAPI URL polled by `metrics-exporter` |
| `COLLECT_INTERVAL` | `15` | Scrape interval in seconds for `metrics-exporter` |
| `EXPORTER_PORT` | `9100` | HTTP port exposed by `metrics-exporter` |

### Detection config (live, via dashboard or API)

| Parameter | Default | Description |
| --- | --- | --- |
| `enabled` | `true` | Master detection switch |
| `schedule_enabled` | `false` | Restrict detection to a time window |
| `schedule_start` | `20:00` | Start of active window (`HH:MM`) |
| `schedule_end` | `06:00` | End of active window (`HH:MM`, supports overnight) |
| `conf_threshold` | `0.35` | Minimum YOLO confidence score (0–1) |

---

## Bill of Materials

The hardware below is what this build was tested on. Amazon.de links are provided for convenience — **specialist distributors are often cheaper and ship faster within Germany/EU** (see alternatives per item).

> Prices are approximate and subject to change. Verify current availability on each shop's website before ordering.

---

### 🖥️ Raspberry Pi 5 — 16 GB

The 16 GB variant gives comfortable headroom for YOLO inference, Docker, Prometheus, and the OTel Collector running simultaneously.

The Raspberry Pi Foundation enforces MSRP — prices are nearly identical everywhere. Buy from whoever has stock and lowest shipping to you.

| | |
| --- | --- |
| **Amazon.de** | [Raspberry Pi 5 — 16 GB](https://www.amazon.de/Raspberry-Pi-5-16-GB/dp/B0DSPYPKRG) |
| **Berrybase** (DE, official reseller, ships next day) | [berrybase.de/raspberry-pi-5-16gb](https://www.berrybase.de) |
| **Reichelt** (DE, ships next day) | [reichelt.de](https://www.reichelt.de) — search "RPI5-16GB" |
| **Botland** (PL/EU, ships to DE) | [botland.de](https://botland.de) — search "Raspberry Pi 5 16GB" |
| **Approx. price** | ~€ 139 – 149 |

---

### 🏠 Case with NVMe SSD HAT + PoE — GeeekPi

Aluminium enclosure with integrated M.2 NVMe HAT and PoE support for Pi 5. Passive cooling built in.

Alternatives are often sold as separate HATs + case — mixing an **official Raspberry Pi M.2 HAT+** (~€15) with a separate PoE HAT gives the best compatibility at the lowest cost.

| | |
| --- | --- |
| **Amazon.de** | [GeeekPi Pi 5 Alu Case + NVMe HAT + PoE](https://www.amazon.de/GeeekPi-Raspberry-offiziellem-Aluminiumgehäuse-unterstützt-Schwarz/dp/B0DMW98LBR) |
| **Berrybase** — Argon ONE V3 M.2 case | [berrybase.de](https://www.berrybase.de) — includes M.2 slot, add separate PoE HAT (~€35–45) |
| **Berrybase / Reichelt** — Official RPi M.2 HAT+ | Official HAT, most compatible; add separate PoE HAT (~€15 + PoE HAT) |
| **Waveshare combo** (via Berrybase or AliExpress EU) | Combined NVMe + PoE HAT for Pi 5 (~€25–40) |
| **Approx. price** | ~€ 35 – 60 depending on variant |

---

### 💾 NVMe SSD — 256 GB (M.2 2230 / 2242)

Use an M.2 **2230** or **2242** form factor — full-size 2280 drives do not fit most Pi HATs. Recommended drives: **Samsung PM991a 256 GB** (2230) or **WD SN740 256 GB** (2230) — both well-tested with Pi 5 HATs.

| | |
| --- | --- |
| **Amazon.de** | [256 GB NVMe SSD M.2](https://www.amazon.de/dp/B0CP9BZLZ5) |
| **Berrybase** (DE) | WD SN740 or compatible 2230/2242 SSD — [berrybase.de](https://www.berrybase.de) (~€30–45) |
| **Reichelt** (DE) | Kingston NVMe 256 GB — [reichelt.de](https://www.reichelt.de) (~€25–35) |
| **Alternate.de / Mindfactory.de** | Samsung PM991a 256 GB M.2 2230 (OEM, often cheapest) (~€25–35) |
| **Approx. price** | ~€ 25 – 45 |

---

### ⚡ Power Supply — 5 V / 5 A USB-C (27 W)

> **Critical — do not skip this:** The Pi 5 with an NVMe HAT under YOLO inference load draws up to ~15–18 W peak. A standard 3 A (15 W) USB-C supply **will cause random hard crashes** — undervoltage events reset the CPU before any software can log them. The official **Raspberry Pi 27 W USB-C PSU** (~€12–14) is the safest and cheapest option.

| | |
| --- | --- |
| **Amazon.de** | [5 V / 5 A USB-C Power Supply](https://www.amazon.de/dp/B0CQYVZYR6) |
| **Official RPi 27 W PSU** at Berrybase | [berrybase.de](https://www.berrybase.de) — search "Raspberry Pi 27W USB-C Netzteil" (~€12–14) |
| **Official RPi 27 W PSU** at Reichelt | [reichelt.de](https://www.reichelt.de) — search "RPI 27W" or "RPI PS USB-C" (~€12–15) |
| **Conrad** (DE, also in-store) | [conrad.de](https://www.conrad.de) — search "Raspberry Pi Netzteil USB-C 5A" (~€13–16) |
| **Approx. price** | ~€ 12 – 16 |

---

### 📷 Night Vision Outdoor Webcam (USB)

USB camera with IR night vision for outdoor use. Positioned to view the garden — connected directly to the Pi via USB.

Alternative: the **Raspberry Pi Camera Module 3 NoIR** (~€30 at Berrybase) + a cheap IR illuminator (~€8–15 on Amazon) gives better image quality and is purpose-built for the Pi.

| | |
| --- | --- |
| **Amazon.de** | [Night Vision Outdoor Webcam](https://www.amazon.de/dp/B0194ZILNY) |
| **ELP direct / AliExpress EU warehouse** | Same or similar ELP USB IR cameras, often cheaper than Amazon resellers (~€25–50) |
| **Berrybase** — RPi Camera Module 3 NoIR + IR illuminator | [berrybase.de](https://www.berrybase.de) — best image quality option for Pi (~€35–50 total) |
| **Botland.de** — ArduCam USB IR cameras | [botland.de](https://botland.de) — USB cameras with IR, ships to DE (~€20–45) |
| **Approx. price** | ~€ 25 – 60 depending on resolution and IR range |

---

### 💰 Approximate Total

| Component | Approx. price |
| --- | --- |
| Raspberry Pi 5 16 GB | ~€ 145 |
| Case + NVMe HAT + PoE | ~€ 45 |
| NVMe SSD 256 GB (Samsung PM991a / WD SN740) | ~€ 30 |
| 5 V / 5 A Power Supply (official RPi 27 W PSU) | ~€ 13 |
| Night Vision Webcam | ~€ 35 |
| **Total** | **~€ 268** |

> Buying through Berrybase, Reichelt, or Botland instead of Amazon typically saves €10–20 overall and ships the next business day within Germany.

---

## Detected Classes

Night Watcher reports **people** and **animals** from the COCO dataset:

`person` · `bird` · `cat` · `dog` · `horse` · `sheep` · `cow` · `elephant` · `bear` · `zebra` · `giraffe`

---

## Roadmap

- Add two DHT22 sensors on the Raspberry Pi GPIO header to measure ambient temperature and humidity as well as enclosure temperature and humidity, enabling condensation alerts and a controlled Raspberry Pi shutdown when enclosure conditions become unsafe.
- Add VPN access for secure remote administration and dashboard access.
- Add photo documentation of the mechanical setup.

---

## License

This project is licensed under the MIT License. You may use, modify, and
redistribute it freely, including commercially, as long as the copyright and
license notice are retained.

If you reference this project in documentation, research, or derivative work,
use the citation metadata in [`CITATION.cff`](CITATION.cff).

---

## Project Documentation

| Document | Description |
| --- | --- |
| [`docs/RUN_REPORTS.md`](docs/RUN_REPORTS.md) | Long-term health reports with statistics and time-series charts — regenerated by `scripts/analyze_health.py` |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | Versioned release plan (v1.1 through v2.1) |
| [`docs/BUILD.md`](docs/BUILD.md) | Hardware build and assembly photo documentation |
