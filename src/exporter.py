"""Standalone Prometheus metrics exporter for Night Watcher.

Runs as an independent Docker service on port 9100, collecting:
  - Hardware metrics directly via psutil and /sys (CPU, memory, disk, temperature)
  - PMIC voltage/current readings via vcgencmd (Raspberry Pi 5)
  - Application metrics by polling the FastAPI service at /metrics/app
  - Log statistics from the shared SQLite database

This service is completely independent of the Streamlit dashboard — metrics
accumulate in Prometheus across app restarts and are never lost.

Usage (standalone):
    python -m src.exporter

Environment variables:
    NIGHT_WATCHER_URL     FastAPI service base URL (default: http://night-watcher:8000)
    ASSETS_DIR            Path to shared assets volume (default: /assets)
    COLLECT_INTERVAL      Scrape interval in seconds (default: 15)
    EXPORTER_PORT         HTTP port to expose /metrics on (default: 9100)
"""
from __future__ import annotations

import logging
import os
import sqlite3
import subprocess
import time
from pathlib import Path

import psutil
import requests
from prometheus_client import Gauge, Info, start_http_server

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("night_watcher.exporter")

_NIGHT_WATCHER_URL = os.environ.get("NIGHT_WATCHER_URL", "http://night-watcher:8000")
_ASSETS_DIR = Path(os.environ.get("ASSETS_DIR", "/assets"))
_LOG_DB = _ASSETS_DIR / "logs" / "app.db"
_COLLECT_INTERVAL = int(os.environ.get("COLLECT_INTERVAL", "15"))
_PORT = int(os.environ.get("EXPORTER_PORT", "9100"))

# ---------------------------------------------------------------------------
# Metric registry
# ---------------------------------------------------------------------------

# -- Hardware ----------------------------------------------------------------
hw_cpu_percent = Gauge(
    "night_watcher_hw_cpu_percent",
    "CPU utilization (%)",
)
hw_memory_percent = Gauge(
    "night_watcher_hw_memory_percent",
    "RAM utilization (%)",
)
hw_memory_used_mb = Gauge(
    "night_watcher_hw_memory_used_mb",
    "RAM used (MB)",
)
hw_disk_percent = Gauge(
    "night_watcher_hw_disk_percent",
    "Disk utilization (%) on the assets volume",
)
hw_disk_used_gb = Gauge(
    "night_watcher_hw_disk_used_gb",
    "Disk space used (GB) on the assets volume",
)
hw_temperature_c = Gauge(
    "night_watcher_hw_temperature_c",
    "CPU temperature (°C)",
)
hw_cpu_freq_mhz = Gauge(
    "night_watcher_hw_cpu_freq_mhz",
    "CPU clock frequency (MHz)",
)
hw_uptime_seconds = Gauge(
    "night_watcher_hw_uptime_seconds",
    "System uptime (seconds since boot)",
)

# -- PMIC (Raspberry Pi 5) ---------------------------------------------------
pmic_ext5v_v = Gauge(
    "night_watcher_pmic_ext5v_v",
    "USB-C power supply input voltage (V)",
)
pmic_total_power_w = Gauge(
    "night_watcher_pmic_total_power_w",
    "Total estimated system power across all PMIC rails (W)",
)
pmic_under_voltage = Gauge(
    "night_watcher_pmic_under_voltage",
    "Under-voltage flag: 1 if input voltage < 4.75 V",
)
pmic_rail_voltage_v = Gauge(
    "night_watcher_pmic_rail_voltage_v",
    "PMIC rail voltage (V)",
    ["rail"],
)
pmic_rail_current_a = Gauge(
    "night_watcher_pmic_rail_current_a",
    "PMIC rail current (A)",
    ["rail"],
)

# -- Application (polled from FastAPI) ---------------------------------------
app_service_up = Gauge(
    "night_watcher_app_service_up",
    "FastAPI service reachability: 1 = up, 0 = down",
)
app_frames_total = Gauge(
    "night_watcher_app_frames_total",
    "Total camera frames processed since service start",
)
app_fps_avg = Gauge(
    "night_watcher_app_fps_avg",
    "Average frames-per-second over the full service uptime",
)
app_avg_processing_ms = Gauge(
    "night_watcher_app_avg_processing_ms",
    "Average YOLO inference + annotation latency (ms)",
)
app_sessions_total = Gauge(
    "night_watcher_app_sessions_total",
    "Total detection sessions started since service start",
)
app_detections_total = Gauge(
    "night_watcher_app_detections_total",
    "Total object detections by class since service start",
    ["class_name"],
)
app_uptime_seconds = Gauge(
    "night_watcher_app_uptime_seconds",
    "FastAPI service uptime (seconds)",
)

# -- Logs (from SQLite) ------------------------------------------------------
log_count_total = Gauge(
    "night_watcher_log_count_total",
    "Total log entries in the database by level",
    ["level"],
)
log_errors_last_5m = Gauge(
    "night_watcher_log_errors_last_5m",
    "ERROR and CRITICAL log entries written in the last 5 minutes",
)


# ---------------------------------------------------------------------------
# Hardware collection
# ---------------------------------------------------------------------------

def _read_temperature() -> float | None:
    """Read CPU temperature from /sys/class/thermal or psutil."""
    try:
        thermal = Path("/sys/class/thermal/thermal_zone0/temp")
        if thermal.exists():
            return round(int(thermal.read_text().strip()) / 1000.0, 1)
    except Exception:
        pass
    try:
        temps = psutil.sensors_temperatures()  # type: ignore[attr-defined]
        for entries in temps.values():
            if entries:
                return round(entries[0].current, 1)
    except Exception:
        pass
    return None


def collect_hardware() -> None:
    hw_cpu_percent.set(psutil.cpu_percent(interval=0.5))

    mem = psutil.virtual_memory()
    hw_memory_percent.set(mem.percent)
    hw_memory_used_mb.set(round(mem.used / 1024**2, 1))

    disk_path = str(_ASSETS_DIR) if _ASSETS_DIR.exists() else "/"
    disk = psutil.disk_usage(disk_path)
    hw_disk_percent.set(disk.percent)
    hw_disk_used_gb.set(round(disk.used / 1024**3, 2))

    temp = _read_temperature()
    if temp is not None:
        hw_temperature_c.set(temp)

    freq = psutil.cpu_freq()
    if freq:
        hw_cpu_freq_mhz.set(round(freq.current, 1))

    hw_uptime_seconds.set(round(time.time() - psutil.boot_time()))


# ---------------------------------------------------------------------------
# PMIC collection
# ---------------------------------------------------------------------------

def collect_pmic() -> None:
    try:
        result = subprocess.run(
            ["vcgencmd", "pmic_read_adc"],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except FileNotFoundError:
        return  # not a Raspberry Pi / vcgencmd not mounted
    except subprocess.TimeoutExpired:
        logger.debug("vcgencmd pmic_read_adc timed out")
        return

    voltages: dict[str, float] = {}
    currents: dict[str, float] = {}

    for line in result.stdout.strip().splitlines():
        line = line.strip()
        if "=" not in line:
            continue
        name_part, val_part = line.split("=", 1)
        val_str = val_part.strip().rstrip("AV").strip()
        try:
            val = float(val_str)
        except ValueError:
            continue
        parts = name_part.strip().split()
        if not parts:
            continue
        rail_full = parts[0]
        if rail_full.endswith("_A"):
            currents[rail_full[:-2]] = val
        elif rail_full.endswith("_V"):
            voltages[rail_full[:-2]] = val

    for rail in set(voltages) | set(currents):
        pmic_rail_voltage_v.labels(rail=rail).set(voltages.get(rail, 0.0))
        pmic_rail_current_a.labels(rail=rail).set(currents.get(rail, 0.0))

    total_power = sum(
        voltages[r] * currents[r] for r in set(voltages) & set(currents)
    )
    pmic_total_power_w.set(round(total_power, 3))

    ext5v = voltages.get("EXT5V")
    if ext5v is not None:
        pmic_ext5v_v.set(round(ext5v, 4))
        pmic_under_voltage.set(1 if ext5v < 4.75 else 0)


# ---------------------------------------------------------------------------
# App metrics collection
# ---------------------------------------------------------------------------

def collect_app_metrics() -> None:
    try:
        resp = requests.get(f"{_NIGHT_WATCHER_URL}/metrics/app", timeout=3.0)
        resp.raise_for_status()
        data: dict = resp.json()
        app_service_up.set(1)
        app_frames_total.set(data.get("frames_total", 0))
        app_fps_avg.set(data.get("fps_avg", 0.0))
        app_avg_processing_ms.set(data.get("avg_processing_ms", 0.0))
        app_sessions_total.set(data.get("sessions_total", 0))
        app_uptime_seconds.set(data.get("uptime_seconds", 0))
        for cls, count in data.get("detections_by_class", {}).items():
            app_detections_total.labels(class_name=cls).set(count)
    except requests.exceptions.ConnectionError:
        app_service_up.set(0)
    except Exception as exc:
        logger.debug("App metrics unavailable: %s", exc)
        app_service_up.set(0)


# ---------------------------------------------------------------------------
# Log metrics collection
# ---------------------------------------------------------------------------

def collect_log_metrics() -> None:
    if not _LOG_DB.exists():
        return
    try:
        con = sqlite3.connect(str(_LOG_DB), timeout=5.0)
        try:
            for level, count in con.execute(
                "SELECT level, COUNT(*) FROM logs GROUP BY level"
            ).fetchall():
                log_count_total.labels(level=level.upper()).set(count)

            since = time.time() - 300  # last 5 minutes
            (errors,) = con.execute(
                "SELECT COUNT(*) FROM logs"
                " WHERE level IN ('ERROR','CRITICAL') AND timestamp >= ?",
                (since,),
            ).fetchone()
            log_errors_last_5m.set(errors)
        finally:
            con.close()
    except Exception as exc:
        logger.debug("Log metrics unavailable: %s", exc)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> None:
    start_http_server(_PORT)
    logger.info(
        "Night Watcher metrics exporter started on :%d (interval=%ds)",
        _PORT,
        _COLLECT_INTERVAL,
    )

    while True:
        t0 = time.monotonic()

        for name, fn in [
            ("hardware", collect_hardware),
            ("pmic", collect_pmic),
            ("app", collect_app_metrics),
            ("logs", collect_log_metrics),
        ]:
            try:
                fn()
            except Exception as exc:
                logger.error("%s collection failed: %s", name, exc)

        elapsed = time.monotonic() - t0
        time.sleep(max(0.0, _COLLECT_INTERVAL - elapsed))


if __name__ == "__main__":
    main()
