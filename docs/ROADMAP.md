# Night Watcher — Roadmap

Versioned release plan.  Each release is a stable tag on `main`.

---

## v1.3.0 — Logging & Secure Remote Access ✅ Released

> Infrastructure-only.  No application code changes required.

### Logging

| Change | File | Why |
| --- | --- | --- |
| Add Loki log aggregation service | `docker-compose.yml`, `loki/config.yaml` | Central log store for all container stdout/stderr logs, queryable in Grafana |
| Add Promtail log shipper | `docker-compose.yml`, `promtail/config.yaml` | Ships Docker container logs to Loki with container/service labels |
| Provision Loki as Grafana datasource | `grafana/provisioning/datasources/loki.yml` | Grafana → Explore → Loki available with zero manual setup |

### Remote Access & TLS

| Change | File | Why |
| --- | --- | --- |
| Add Tailscale VPN service | `docker-compose.yml`, `scripts/tailscale-start.sh` | Secure remote access to the Pi over a private Tailscale network; issues a TLS certificate via `tailscale cert` |
| Add nginx TLS reverse proxy | `docker-compose.yml`, `nginx/nginx.conf` | Terminates HTTPS (cert from Tailscale) and routes all services behind a single endpoint |
| Add `.env.example` | `.env.example` | Documents required `TS_AUTHKEY`, `TS_HOSTNAME`, and `TS_FQDN` variables |
| Configure Grafana subpath serving | `docker-compose.yml` | `GF_SERVER_SERVE_FROM_SUB_PATH=true` so Grafana works at `/grafana/` behind nginx |
| Configure Prometheus subpath serving | `docker-compose.yml` | `--web.route-prefix=/prometheus` so Prometheus UI works at `/prometheus/` behind nginx |
| Bump version to 1.3.0 | `src/__init__.py`, `pyproject.toml`, `CITATION.cff` | Reflects new infrastructure milestone |

### Access overview (after Tailscale setup)

| URL | Service |
| --- | --- |
| `https://<ts-host>/` | Streamlit dashboard |
| `https://<ts-host>/grafana/` | Grafana (logs + metrics) |
| `https://<ts-host>/api/` | Night Watcher FastAPI |
| `https://<ts-host>/prometheus/` | Prometheus UI |

---

## v1.2.1 — Coverage & Metadata Sync ✅ Released

> Code-only.  No hardware changes required.

### Quality

| Change | File | Why |
| --- | --- | --- |
| Expand `service.py` unit coverage across detection loop, stream, health, and lifespan paths | `tests/test_service.py` | Raises coverage on the service module and locks down branch behavior that previously went untested |
| Centralize runtime version and bump project metadata to `1.2.1` | `src/__init__.py`, `src/service.py`, `src/telemetry.py`, `pyproject.toml`, `CITATION.cff` | Keeps FastAPI, telemetry resources, packaging, and citation metadata aligned for the next patch tag |

---

## v1.2 — Fully Pi-Side Stack ✅ Released

> Code-only.  No hardware changes required.

### Dashboard microservice

| Change | File | Why |
| --- | --- | --- |
| Move Streamlit dashboard from local client to Docker service on the Pi (port 8501) | `docker-compose.yml`, `app.py` | Eliminates local installation — any browser on the LAN can open the dashboard |
| RASPI_URL / GRAFANA_URL use Pi's mDNS hostname; PROMETHEUS_URL uses Docker-internal hostname | `docker-compose.yml`, `app.py` | Browser-embedded content (MJPEG stream, video, Grafana links) must resolve to the Pi's public address; Prometheus queries are server-side and can use the Docker internal network |
| Remove `client` dependency group from pyproject.toml | `pyproject.toml` | All dashboard dependencies are now in the main group; no split needed |
| Remove `--no-group client` flag from Dockerfile | `Dockerfile` | Matches pyproject.toml change; single install step |
| Bump project version to 1.2.0 | `pyproject.toml` | Reflects new deployment model |

### Tests

| Change | File | Why |
| --- | --- | --- |
| Expand test_health.py from 3 to 20 tests | `tests/test_health.py` | `get_system_health`, `_read_temperature`, `get_docker_services`, and all error paths (FileNotFoundError, TimeoutExpired, empty output) were uncovered |

---

## v1.1 — Observability & Frontend Cleanup ✅ Released

> Code-only.  No hardware changes required.

### Metrics

| Change | File | Why |
| --- | --- | --- |
| Add absolute memory metrics: `hw_memory_total_mb`, `hw_memory_available_mb` | `src/exporter.py` | % alone is not actionable — operators need raw MB to size the system |
| Add absolute disk metrics: `hw_disk_total_gb`, `hw_disk_free_gb` | `src/exporter.py` | Disk-full alerts require the free and total values in GB |
| Fix YOLO inference timing — measure inside background worker thread | `src/detector.py`, `src/service.py` | Prior `avg_processing_ms` included camera I/O and was near-zero for async frames; `last_inference_ms` now reflects actual model latency |

### Grafana

| Change | File | Why |
| --- | --- | --- |
| Add Grafana service to Docker Compose (port 3000) | `docker-compose.yml` | Replaces custom Streamlit time-series charts with a production-grade dashboard tool |
| Auto-provision Prometheus datasource | `grafana/provisioning/datasources/prometheus.yml` | No manual setup after `docker compose up` |
| Anonymous viewer access | `docker-compose.yml` | Streamlit can link directly to Grafana panels without requiring credentials |

### Frontend

| Change | File | Why |
| --- | --- | --- |
| Remove all time-series line charts from the Health tab | `app.py` | Charts are now in Grafana; duplicating them in Streamlit adds complexity and stale query logic |
| Add Grafana link at the top of the Health tab | `app.py` | One-click access to historical dashboards |
| Remove redundant frame timestamp from Live Stream status row | `app.py` | Timestamp is already burned into every frame server-side; the metric box was noise |

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
- New Streamlit Health section — "Environmental" panel with live readings.

---

## v1.3 — Remote Access & Security ✅ Delivered in v1.3.0

> Implemented using Tailscale instead of WireGuard for zero-config peer setup.
> See v1.3.0 release notes above.

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
- **Multi-camera support** — generalize `src/service.py` to manage a pool of camera devices, each with its own detection loop and `/stream/{device_id}` endpoint.
- **Detection pipeline performance** — background video writing, async session JSON persistence, adaptive sleep in capture loop.
