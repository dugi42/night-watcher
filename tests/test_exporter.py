"""Tests for the standalone Prometheus metrics exporter (src/exporter.py)."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import psutil
import pytest

from src import exporter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeGauge:
    """Minimal gauge stub that records set() calls."""

    def __init__(self) -> None:
        self.value: float | None = None
        self._children: dict[str, "_FakeGauge"] = {}

    def set(self, v: float) -> None:
        self.value = v

    def labels(self, **kwargs) -> "_FakeGauge":
        key = ",".join(f"{k}={v}" for k, v in sorted(kwargs.items()))
        if key not in self._children:
            self._children[key] = _FakeGauge()
        return self._children[key]


def _patch_all_gauges(monkeypatch) -> dict[str, _FakeGauge]:
    """Replace every module-level gauge in exporter with a _FakeGauge."""
    gauges: dict[str, _FakeGauge] = {}
    gauge_names = [
        "hw_cpu_percent", "hw_memory_percent", "hw_memory_used_mb",
        "hw_disk_percent", "hw_disk_used_gb", "hw_temperature_c",
        "hw_cpu_freq_mhz", "hw_uptime_seconds",
        "pmic_ext5v_v", "pmic_total_power_w", "pmic_under_voltage",
        "pmic_rail_voltage_v", "pmic_rail_current_a",
        "app_service_up", "app_frames_total", "app_fps_avg",
        "app_avg_processing_ms", "app_sessions_total", "app_detections_total",
        "app_uptime_seconds",
        "log_count_total", "log_errors_last_5m",
    ]
    for name in gauge_names:
        g = _FakeGauge()
        gauges[name] = g
        monkeypatch.setattr(exporter, name, g)
    return gauges


# ---------------------------------------------------------------------------
# _read_temperature
# ---------------------------------------------------------------------------

def test_read_temperature_reads_thermal_zone(monkeypatch, tmp_path):
    thermal = tmp_path / "temp"
    thermal.write_text("63500\n")

    class _FakePath:
        def __init__(self, p: str) -> None:
            self._p = p

        def exists(self) -> bool:
            return self._p == "/sys/class/thermal/thermal_zone0/temp"

        def read_text(self) -> str:
            return "63500\n"

    monkeypatch.setattr(exporter, "Path", _FakePath)
    assert exporter._read_temperature() == 63.5


def test_read_temperature_falls_back_to_psutil(monkeypatch):
    class _FakePath:
        def __init__(self, p: str) -> None:
            self._p = p

        def exists(self) -> bool:
            return False

    monkeypatch.setattr(exporter, "Path", _FakePath)
    # sensors_temperatures is Linux-only; add it with raising=False so the
    # test works on macOS too.
    monkeypatch.setattr(
        exporter.psutil,
        "sensors_temperatures",
        lambda: {"cpu_thermal": [SimpleNamespace(current=71.2)]},
        raising=False,
    )
    assert exporter._read_temperature() == 71.2


def test_read_temperature_returns_none_when_unavailable(monkeypatch):
    class _FakePath:
        def __init__(self, p: str) -> None:
            pass

        def exists(self) -> bool:
            return False

    monkeypatch.setattr(exporter, "Path", _FakePath)
    monkeypatch.setattr(exporter.psutil, "sensors_temperatures", lambda: {}, raising=False)
    assert exporter._read_temperature() is None


# ---------------------------------------------------------------------------
# collect_hardware
# ---------------------------------------------------------------------------

def test_collect_hardware_sets_all_gauges(monkeypatch):
    gauges = _patch_all_gauges(monkeypatch)

    monkeypatch.setattr(psutil, "cpu_percent", lambda interval=None: 42.0)
    monkeypatch.setattr(
        psutil,
        "virtual_memory",
        lambda: SimpleNamespace(percent=61.0, used=2 * 1024**2),
    )
    monkeypatch.setattr(
        psutil,
        "disk_usage",
        lambda path: SimpleNamespace(percent=33.0, used=8 * 1024**3),
    )
    monkeypatch.setattr(psutil, "cpu_freq", lambda: SimpleNamespace(current=1800.0))
    monkeypatch.setattr(psutil, "boot_time", lambda: time.time() - 3600)
    monkeypatch.setattr(exporter, "_read_temperature", lambda: 55.3)

    # _ASSETS_DIR.exists() is called inside collect_hardware via Path logic;
    # patch the module-level constant so disk_usage is called predictably.
    monkeypatch.setattr(exporter, "_ASSETS_DIR", Path("/assets"))

    exporter.collect_hardware()

    assert gauges["hw_cpu_percent"].value == 42.0
    assert gauges["hw_memory_percent"].value == 61.0
    assert gauges["hw_memory_used_mb"].value == 2.0
    assert gauges["hw_disk_percent"].value == 33.0
    assert gauges["hw_disk_used_gb"].value == 8.0
    assert gauges["hw_temperature_c"].value == 55.3
    assert gauges["hw_cpu_freq_mhz"].value == 1800.0
    assert gauges["hw_uptime_seconds"].value is not None


def test_collect_hardware_skips_temperature_when_none(monkeypatch):
    gauges = _patch_all_gauges(monkeypatch)

    monkeypatch.setattr(psutil, "cpu_percent", lambda interval=None: 10.0)
    monkeypatch.setattr(
        psutil, "virtual_memory", lambda: SimpleNamespace(percent=20.0, used=512 * 1024**2)
    )
    monkeypatch.setattr(
        psutil, "disk_usage", lambda p: SimpleNamespace(percent=5.0, used=1 * 1024**3)
    )
    monkeypatch.setattr(psutil, "cpu_freq", lambda: None)
    monkeypatch.setattr(psutil, "boot_time", lambda: time.time() - 100)
    monkeypatch.setattr(exporter, "_read_temperature", lambda: None)
    monkeypatch.setattr(exporter, "_ASSETS_DIR", Path("/nonexistent"))

    exporter.collect_hardware()

    # Temperature gauge must remain untouched when reading returns None
    assert gauges["hw_temperature_c"].value is None
    # No cpu_freq → gauge not set
    assert gauges["hw_cpu_freq_mhz"].value is None


# ---------------------------------------------------------------------------
# collect_pmic
# ---------------------------------------------------------------------------

def test_collect_pmic_parses_output_and_sets_gauges(monkeypatch):
    gauges = _patch_all_gauges(monkeypatch)

    vcg_output = "\n".join([
        "EXT5V_V volt(0)=5.01000000V",
        "EXT5V_A current(1)=0.80000000A",
        "VDD_CORE_V volt(2)=0.90000000V",
        "VDD_CORE_A current(3)=1.20000000A",
    ])
    monkeypatch.setattr(
        exporter.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(stdout=vcg_output),
    )

    exporter.collect_pmic()

    assert gauges["pmic_ext5v_v"].value == 5.01
    assert gauges["pmic_under_voltage"].value == 0  # 5.01 >= 4.75
    assert gauges["pmic_total_power_w"].value == pytest.approx(5.088, rel=1e-3)
    assert gauges["pmic_rail_voltage_v"]._children["rail=EXT5V"].value == 5.01
    assert gauges["pmic_rail_current_a"]._children["rail=VDD_CORE"].value == 1.2


def test_collect_pmic_sets_under_voltage_flag(monkeypatch):
    gauges = _patch_all_gauges(monkeypatch)

    vcg_output = "EXT5V_V volt(0)=4.60000000V\nEXT5V_A current(1)=0.50000000A\n"
    monkeypatch.setattr(
        exporter.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(stdout=vcg_output),
    )

    exporter.collect_pmic()

    assert gauges["pmic_under_voltage"].value == 1


def test_collect_pmic_silently_skips_when_vcgencmd_missing(monkeypatch):
    gauges = _patch_all_gauges(monkeypatch)

    def _raise(*a, **kw):
        raise FileNotFoundError("vcgencmd not found")

    monkeypatch.setattr(exporter.subprocess, "run", _raise)

    exporter.collect_pmic()  # must not raise

    assert gauges["pmic_ext5v_v"].value is None


def test_collect_pmic_silently_skips_on_timeout(monkeypatch):
    gauges = _patch_all_gauges(monkeypatch)

    def _raise(*a, **kw):
        raise exporter.subprocess.TimeoutExpired(cmd="vcgencmd", timeout=2)

    monkeypatch.setattr(exporter.subprocess, "run", _raise)

    exporter.collect_pmic()  # must not raise

    assert gauges["pmic_ext5v_v"].value is None


# ---------------------------------------------------------------------------
# collect_app_metrics
# ---------------------------------------------------------------------------

def test_collect_app_metrics_sets_gauges_on_success(monkeypatch):
    gauges = _patch_all_gauges(monkeypatch)

    payload = {
        "frames_total": 1200,
        "fps_avg": 19.5,
        "avg_processing_ms": 48.2,
        "sessions_total": 7,
        "uptime_seconds": 3600,
        "detections_by_class": {"bird": 42, "cat": 5},
    }

    fake_resp = SimpleNamespace(
        status_code=200,
        raise_for_status=lambda: None,
        json=lambda: payload,
    )
    monkeypatch.setattr(exporter.requests, "get", lambda url, timeout=None: fake_resp)

    exporter.collect_app_metrics()

    assert gauges["app_service_up"].value == 1
    assert gauges["app_frames_total"].value == 1200
    assert gauges["app_fps_avg"].value == 19.5
    assert gauges["app_avg_processing_ms"].value == 48.2
    assert gauges["app_sessions_total"].value == 7
    assert gauges["app_uptime_seconds"].value == 3600
    assert gauges["app_detections_total"]._children["class_name=bird"].value == 42
    assert gauges["app_detections_total"]._children["class_name=cat"].value == 5


def test_collect_app_metrics_marks_service_down_on_connection_error(monkeypatch):
    gauges = _patch_all_gauges(monkeypatch)

    import requests as req_mod

    def _raise(url, timeout=None):
        raise req_mod.exceptions.ConnectionError("refused")

    monkeypatch.setattr(exporter.requests, "get", _raise)

    exporter.collect_app_metrics()

    assert gauges["app_service_up"].value == 0


def test_collect_app_metrics_marks_service_down_on_any_error(monkeypatch):
    gauges = _patch_all_gauges(monkeypatch)

    monkeypatch.setattr(exporter.requests, "get", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")))

    exporter.collect_app_metrics()

    assert gauges["app_service_up"].value == 0


# ---------------------------------------------------------------------------
# collect_log_metrics
# ---------------------------------------------------------------------------

def test_collect_log_metrics_counts_by_level(monkeypatch, tmp_path):
    db = tmp_path / "app.db"
    con = sqlite3.connect(str(db))
    con.execute(
        "CREATE TABLE logs (id INTEGER PRIMARY KEY, timestamp REAL, level TEXT, logger TEXT, message TEXT)"
    )
    now = time.time()
    rows = [
        (now - 10, "INFO", "app", "msg"),
        (now - 20, "INFO", "app", "msg"),
        (now - 30, "WARNING", "app", "msg"),
        (now - 60, "ERROR", "app", "err"),
        (now - 400, "ERROR", "app", "old error"),  # outside 5-min window
    ]
    con.executemany("INSERT INTO logs (timestamp,level,logger,message) VALUES (?,?,?,?)", rows)
    con.commit()
    con.close()

    gauges = _patch_all_gauges(monkeypatch)
    monkeypatch.setattr(exporter, "_LOG_DB", db)

    exporter.collect_log_metrics()

    assert gauges["log_count_total"]._children["level=INFO"].value == 2
    assert gauges["log_count_total"]._children["level=WARNING"].value == 1
    assert gauges["log_count_total"]._children["level=ERROR"].value == 2  # both rows total
    assert gauges["log_errors_last_5m"].value == 1  # only the recent error


def test_collect_log_metrics_skips_when_db_missing(monkeypatch, tmp_path):
    gauges = _patch_all_gauges(monkeypatch)
    monkeypatch.setattr(exporter, "_LOG_DB", tmp_path / "nonexistent.db")

    exporter.collect_log_metrics()  # must not raise

    assert gauges["log_count_total"].value is None


# ---------------------------------------------------------------------------
# main loop (smoke test)
# ---------------------------------------------------------------------------

def test_main_starts_server_and_runs_one_iteration(monkeypatch):
    server_started: list[int] = []
    iterations: list[int] = []

    monkeypatch.setattr(exporter, "start_http_server", lambda port: server_started.append(port))
    monkeypatch.setattr(exporter, "_PORT", 9100)
    monkeypatch.setattr(exporter, "_COLLECT_INTERVAL", 0)
    monkeypatch.setattr(exporter, "collect_hardware", lambda: iterations.append("hw"))
    monkeypatch.setattr(exporter, "collect_pmic", lambda: iterations.append("pmic"))
    monkeypatch.setattr(exporter, "collect_app_metrics", lambda: iterations.append("app"))
    monkeypatch.setattr(exporter, "collect_log_metrics", lambda: iterations.append("log"))

    call_count = {"n": 0}

    original_sleep = time.sleep

    def _sleep(t):
        call_count["n"] += 1
        if call_count["n"] >= 1:
            raise SystemExit

    monkeypatch.setattr(exporter.time, "sleep", _sleep)

    with pytest.raises(SystemExit):
        exporter.main()

    assert server_started == [9100]
    assert "hw" in iterations
    assert "pmic" in iterations
    assert "app" in iterations
    assert "log" in iterations


def test_main_continues_after_collector_error(monkeypatch):
    """A failing collector must not crash the main loop."""
    monkeypatch.setattr(exporter, "start_http_server", lambda port: None)
    monkeypatch.setattr(exporter, "_PORT", 9100)
    monkeypatch.setattr(exporter, "_COLLECT_INTERVAL", 0)
    monkeypatch.setattr(exporter, "collect_hardware", lambda: (_ for _ in ()).throw(RuntimeError("hw fail")))
    monkeypatch.setattr(exporter, "collect_pmic", lambda: None)
    monkeypatch.setattr(exporter, "collect_app_metrics", lambda: None)
    monkeypatch.setattr(exporter, "collect_log_metrics", lambda: None)

    call_count = {"n": 0}

    def _sleep(t):
        call_count["n"] += 1
        if call_count["n"] >= 1:
            raise SystemExit

    monkeypatch.setattr(exporter.time, "sleep", _sleep)

    with pytest.raises(SystemExit):
        exporter.main()
