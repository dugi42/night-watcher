# 🦉 Night Watcher — Auge der Nacht

A Raspberry Pi wildlife camera that uses YOLO object detection to spot animals
in your garden, record video clips, and present statistics and health metrics
in a web dashboard.

![Night Watcher Logo](assets/logo.jpeg)

---

## Stable Release 1.0.0

Night Watcher `1.0.0` is the first stable release of the project. The current
system delivers a complete Raspberry Pi wildlife monitoring stack with:

- Real-time YOLO11n detection for people and animals.
- Session-based annotated video recording with persistent metadata storage.
- A Streamlit dashboard for live video, detections, logs, and system health.
- OpenTelemetry and Prometheus observability for runtime, thermal, and power metrics.
- Docker Compose deployment for repeatable operation on the Raspberry Pi.

---

## Architecture

```text
┌────────────────────────────────────────────┐        ┌──────────────────────────────┐
│  Raspberry Pi  (Docker Compose)            │  HTTP  │  Your laptop / desktop       │
│                                            │◄──────►│                              │
│  ┌─────────────────────────────────────┐   │  :8000 │  streamlit run app.py        │
│  │  night-watcher                      │   │        │                              │
│  │  Camera → YOLO11n → Tracker         │   │        │  📷 Live MJPEG stream        │
│  │               ↓           ↓         │   │        │  📊 Detection statistics     │
│  │          /assets/video   JSON       │   │        │  🩺 Health monitoring        │
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
- Ships detection metrics, system health, and PMIC readings to the **OpenTelemetry Collector** via OTLP HTTP.
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
| `otel-collector` | `4317` (gRPC), `4318` (HTTP), `9464` (Prometheus scrape) | Receives OTLP, exposes metrics |
| `prometheus` | `9090` | Scrapes and stores time-series metrics (30-day retention) |

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
# Default — connects to raspberrypi.local:8000 (override via RASPI_URL)
streamlit run app.py

# Custom Pi address
RASPI_URL=http://<your-pi-ip>:8000 streamlit run app.py

# Custom Prometheus address (for historical charts in the Health tab)
PROMETHEUS_URL=http://<your-pi-ip>:9090 streamlit run app.py
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

Auto-refreshes every 5–10 seconds using Streamlit fragments. Time-series
charts query Prometheus for historical data (configurable window: 15 min to
7 days); falls back to an in-memory rolling window when Prometheus is
unreachable.

| Section | Data source | Refresh |
| --- | --- | --- |
| Power & Throttle Status | `/health/power` | 10 s |
| Voltage & Current (PMIC) | `/health/pmic` | 5 s |
| System Health | `/health/detailed` | 5 s |
| Docker Services | `/health/docker` | 10 s |
| Application Metrics | `/metrics/app` | 5 s |
| Application Logs | `/logs` | 10 s |

**Power & Throttle Status** shows current and historical throttling flags
(under-voltage, frequency-capping, thermal soft limit) read via `vcgencmd`.

**Voltage & Current (PMIC)** shows live USB-C input voltage, total system
power, and CPU core voltage / current from the MXL7704 PMIC ADC channels,
plus a per-rail breakdown table and historical trend charts.

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

### OpenTelemetry

The app uses the **OpenTelemetry SDK** (`opentelemetry-sdk`) to emit:

| Signal | Instruments |
| --- | --- |
| **Detection metrics** | `night_watcher.frames.processed` (counter), `night_watcher.frames.processing_ms` (histogram), `night_watcher.detections.total` (counter by class), `night_watcher.sessions.started` (counter) |
| **System health** | `system.cpu.percent`, `system.memory.percent`, `system.disk.percent`, `system.temperature_c`, `system.cpu.frequency_mhz` (observable gauges, polled every 15 s) |
| **PMIC power** | `pmic.ext5v_v`, `pmic.total_power_w`, `pmic.rail.voltage_v{rail}`, `pmic.rail.current_a{rail}` (observable gauges, polled every 15 s) |
| Traces | Span exporter to OTel Collector (for future instrumentation) |

Metrics are exported every 15 s via **OTLP HTTP** to the `otel-collector`
service, which exposes them on a Prometheus scrape endpoint (`:9464`).

If the OTel Collector is unreachable at startup, the SDK silently falls back
to a no-op provider — the application keeps running.

### Prometheus

Prometheus scrapes the OTel Collector every 15 s and retains data for **30
days**.  Access the UI at `http://<your-pi-hostname>:9090`.

Metric names in Prometheus (the otel-collector config adds the `night_watcher` namespace):

```promql
# System health
night_watcher_system_cpu_percent
night_watcher_system_memory_percent
night_watcher_system_disk_percent
night_watcher_system_temperature_c
night_watcher_system_cpu_frequency_mhz

# PMIC power rails
night_watcher_pmic_ext5v_v
night_watcher_pmic_total_power_w
night_watcher_pmic_rail_voltage_v{rail="VDD_CORE"}
night_watcher_pmic_rail_current_a{rail="VDD_CORE"}

# Detection pipeline
night_watcher_night_watcher_frames_processed_total
night_watcher_night_watcher_detections_total
```

Example queries:

```promql
# CPU temperature over the last hour
night_watcher_system_temperature_c

# Input voltage — detect brown-outs
min_over_time(night_watcher_pmic_ext5v_v[1h])

# CPU core current P95 over the last 5 minutes
histogram_quantile(0.95, rate(night_watcher_pmic_rail_current_a_bucket{rail="VDD_CORE"}[5m]))

# Total system power
night_watcher_pmic_total_power_w
```

### Structured Logs (SQLite)

All `night_watcher.*` logger output is captured by `SQLiteLogHandler` and
written to `/assets/logs/app.db`.  The `/logs` endpoint lets the Streamlit
dashboard query recent entries without SSH access to the Pi.

---

## Long-term Health Analysis

After collecting data for a few days, run the analysis script on your laptop
to generate a health report:

```bash
uv run scripts/analyze_health.py \
    --prometheus http://<your-pi-hostname>:9090 \
    --days 7

# Preview without modifying README
uv run scripts/analyze_health.py --no-write
```

The script queries Prometheus, computes statistics (min / avg / max / P95),
detects notable events (under-voltage, high temperature, CPU / RAM spikes),
and injects the results into the **Long-term Health Report** section below.

---

## Configuration

### Environment variables

| Variable | Default | Description |
| --- | --- | --- |
| `CAMERA_DEVICE` | `/dev/video0` | V4L2 camera device |
| `YOLO_MODEL_PATH` | `/app/models/yolo11n.pt` | YOLO model weights path |
| `ASSETS_DIR` | `/assets` | Root for video, metadata, and logs |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://otel-collector:4318` | OTel Collector HTTP endpoint |
| `RASPI_URL` | `http://raspberrypi.local:8000` | Pi service URL (client-side only) |
| `PROMETHEUS_URL` | `http://raspberrypi.local:9090` | Prometheus URL (client-side only) |

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

## Long-term Health Report

<!-- HEALTH-REPORT-START -->
<!-- Generated by scripts/analyze_health.py on 2026-04-01 19:18 UTC -->

**Generated:** 2026-04-01 19:18 UTC &nbsp;·&nbsp; **Data window:** 7 days

### Resource Summary

| Metric | Min | Avg | Max | P95 |
| --- | ---: | ---: | ---: | ---: |
| CPU utilization | 39.7 % | 48.5 % | 54.4 % | 54.0 % |
| RAM utilization | 8.4 % | 8.7 % | 9.0 % | 8.9 % |
| Disk utilization | 9.8 % | 9.8 % | 9.8 % | 9.8 % |
| CPU temperature | 63.9 °C | 65.1 °C | 67.2 °C | 66.9 °C |
| Input voltage (EXT5V) | 5.059 V | 5.081 V | 5.122 V | 5.109 V |
| System power | 3.69 W | 4.61 W | 5.84 W | 5.73 W |
| CPU core current | 2.027 A | 3.189 A | 4.465 A | 4.449 A |

### Notable Events

- ✅ **No under-voltage events** — EXT5V stayed above 4.75 V (min: 5.059 V)
- ✅ **Temperature healthy** — max 67.2°C, always below 70°C
- ✅ **CPU load healthy** — avg 48.5%, rarely exceeds 90% (0.0% of samples)
- ✅ **RAM healthy** — avg 8.7%, peak 9.0%

### Power Consumption

Average system power: **4.6 W** &nbsp;·&nbsp; Peak: **5.8 W** &nbsp;·&nbsp; P95: **5.7 W**

Peak draw (5.8 W) is 22% of the 27 W PSU — comfortable headroom for the recommended Raspberry Pi 27 W USB-C PSU.

CPU core current: avg **3.189 A**, peak **4.465 A**, P95 **4.449 A**

### Thermal Management

Average temperature: **65.1°C** &nbsp;·&nbsp; Peak: **67.2°C** &nbsp;·&nbsp; P95: **66.9°C**

Temperature is within acceptable range. Occasional spikes under YOLO inference load are normal — the heatsink is working.
<!-- HEALTH-REPORT-END -->
