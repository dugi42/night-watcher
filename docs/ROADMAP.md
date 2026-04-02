# Night Watcher — Roadmap

Versioned release plan.  Each release is a stable tag on `main`.

---

## v1.1 — Performance & Reliability

> Code-only.  No hardware changes required.

### Detection pipeline

| Change | File | Why |
| --- | --- | --- |
| Move `cv2.VideoWriter.write()` to a dedicated background thread | `src/recorder.py` | Each frame write blocks the capture loop for 15–25 ms, causing frame drops during recording sessions |
| Move session JSON persistence off the capture thread | `src/tracker.py` | File I/O on session end blocks the loop for 10–50 ms |
| Adaptive sleep in the capture loop | `src/service.py` | Fixed 50 ms sleep ignores actual frame processing time; replace with `max(0, target_interval - elapsed)` |

### Metrics & health

| Change | File | Why |
| --- | --- | --- |
| Non-blocking CPU sampling in the exporter | `src/exporter.py` | `psutil.cpu_percent(interval=0.5)` blocks for 500 ms every collection cycle; switch to `interval=None` and sample at the *start* of the next cycle |
| Cache `psutil.cpu_percent` in health endpoints | `src/health.py` | `/health/detailed` blocks 200 ms per request; reuse the exporter's latest gauge value instead |
| Cache Prometheus range queries in `st.session_state` | `app.py` | Every Streamlit rerun refetches from Prometheus; a 30 s TTL cache eliminates redundant queries |
| Paginate `/detections` endpoint | `src/service.py` | Full history JSON can be hundreds of KB; add `?limit=&offset=` query parameters |

### Operations

- Add Docker `mem_limit` and `cpus` constraints to `docker-compose.yml` so Prometheus and the OTel Collector cannot starve the detection service under load.

---

## v1.2 — Environmental Monitoring

> Requires two DHT22 sensors wired to the Raspberry Pi GPIO header.

### New hardware

- **Sensor A** — ambient (garden-side): measures outside temperature and humidity.
- **Sensor B** — enclosure (inside the camera box): measures internal temperature and humidity.

### Software

- New `src/sensors.py` module — reads both DHT22 sensors via `adafruit-circuitpython-dht`; caches last reading with a configurable TTL.
- New FastAPI endpoint `/health/environment` — returns current readings from both sensors as JSON.
- Exporter extension — emit four new Prometheus metrics:
  - `night_watcher_env_ambient_temp_c`
  - `night_watcher_env_ambient_humidity_pct`
  - `night_watcher_env_enclosure_temp_c`
  - `night_watcher_env_enclosure_humidity_pct`
- **Condensation-risk alert** — flag in `/health/environment` when enclosure dew point is within 3 °C of enclosure temperature; surface as a warning in the Health tab.
- **Controlled shutdown** — watchdog thread that calls `systemctl poweroff` when enclosure temperature exceeds a configurable threshold (default 65 °C inside the box).
- New Streamlit Health section — "Environmental" panel with live readings and historical charts.

---

## v1.3 — Remote Access & Security

> Code + configuration only.  No new hardware.

### WireGuard VPN

- Add `wg0.conf` template and a `docker-compose.override.yml` that binds service ports to the WireGuard interface only.
- The Pi connects to a VPN server (e.g. a small cloud VM or a home router running WireGuard); the dashboard and Prometheus UI become accessible over the tunnel.
- Document peer configuration for laptop and mobile.

### Authentication

- Optional HTTP basic auth on the Streamlit dashboard via `streamlit-authenticator` and `st.secrets`.
- Single-user config (username + bcrypt hash) in a `.streamlit/secrets.toml` that is gitignored.
- Document how to generate the hash and configure the secrets file.

### Health check for VPN tunnel

- `src/health.py` — add `get_vpn_status()` that reads `wg show` output.
- Surface tunnel state in the Docker Services panel.

---

## v2.0 — Intelligence Upgrade

> Optional new hardware: Raspberry Pi AI HAT+ or ONNX-capable NPU.

### Model upgrade

- Benchmark YOLO11s (small) against YOLO11n (nano) on the Pi 5 with ONNX Runtime export.
- YOLO11n on PyTorch takes 40–200 ms per frame on ARM CPU; YOLO11s with ONNX is approximately 2× faster at the same input resolution.
- Add a `YOLO_EXPORT_FORMAT` environment variable (`pt` / `onnx`) so the Docker image can be built for either backend.

### Persistent object tracking

- Extend `src/tracker.py` with a simple IoU-based multi-object tracker so the same animal receives one stable ID across consecutive frames.
- Session metadata gains a `track_ids` field listing individual object IDs seen during the session.
- Detection statistics tab gains a per-individual sighting count.

### Clip thumbnails

- Extract the highest-confidence annotated frame per session and save it as a JPEG alongside the MP4 (`/assets/thumb/<uuid>.jpg`).
- Surface thumbnails in the Statistics tab without requiring the full video to load.
- New endpoint `GET /thumb/{uuid}` to serve them.

### Night mode

- Measure per-frame luminance histogram after capture.
- When the scene is dark: lower confidence threshold (0.25 vs 0.35), switch JPEG quality to 70, reduce target FPS to 10 to save CPU.
- Expose current mode (`day` / `night`) in `/status`.

---

## v2.1 — Notifications

> Code-only.  Requires a Telegram bot token or ntfy.sh topic.

### Push notifications

- New `src/notifier.py` — async notification worker with pluggable backends (Telegram, ntfy.sh, webhook).
- **Session-start notification** — sent within 5 s of a new detection session: class detected, confidence, thumbnail attached.
- **Daily summary** — scheduled at 08:00 local time: session count, top class, 24 h power and temperature stats.
- **Alert notifications** — triggered by exporter gauges: under-voltage (`pmic_under_voltage == 1`), CPU temperature above 75 °C for 5+ minutes.

### Configuration

- New environment variables: `NOTIFIER_BACKEND` (telegram / ntfy / webhook), `NOTIFIER_TOKEN`, `NOTIFIER_TARGET`.
- Expose notification enable/disable in the detection config API and dashboard toggle.

---

## Pending (unscheduled)

- **Photo documentation of the mechanical build** — see [`BUILD.md`](BUILD.md).
- **Grafana dashboard** — replace the custom Streamlit Health tab charts with a Grafana dashboard JSON provisioned via `docker-compose.yml`.  Grafana renders Prometheus range queries natively and supports alerting rules without custom Python.
- **Multi-camera support** — generalize `src/service.py` to manage a pool of camera devices, each with its own detection loop and `/stream/{device_id}` endpoint.
