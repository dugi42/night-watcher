from __future__ import annotations

from types import SimpleNamespace

import pytest

from src import health


# ---------------------------------------------------------------------------
# _fmt_ports
# ---------------------------------------------------------------------------

def test_fmt_ports_formats_bound_and_exposed_ports() -> None:
    ports = {
        "8000/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8000"}],
        "9090/tcp": None,
    }

    assert health._fmt_ports(ports) == "8000→8000/tcp, 9090/tcp"


def test_fmt_ports_returns_empty_string_for_empty_dict() -> None:
    assert health._fmt_ports({}) == ""


def test_fmt_ports_multiple_bindings() -> None:
    ports = {
        "80/tcp": [
            {"HostIp": "0.0.0.0", "HostPort": "8080"},
            {"HostIp": "0.0.0.0", "HostPort": "8081"},
        ]
    }
    result = health._fmt_ports(ports)
    assert "8080→80/tcp" in result
    assert "8081→80/tcp" in result


# ---------------------------------------------------------------------------
# _read_temperature
# ---------------------------------------------------------------------------

def test_read_temperature_reads_thermal_zone(monkeypatch) -> None:
    class _FakePath:
        def __init__(self, p: str) -> None:
            self._p = p

        def exists(self) -> bool:
            return self._p == "/sys/class/thermal/thermal_zone0/temp"

        def read_text(self) -> str:
            return "71500\n"

    monkeypatch.setattr(health, "Path", _FakePath)
    assert health._read_temperature() == 71.5


def test_read_temperature_falls_back_to_psutil(monkeypatch) -> None:
    class _FakePath:
        def __init__(self, p: str) -> None:
            pass

        def exists(self) -> bool:
            return False

    monkeypatch.setattr(health, "Path", _FakePath)
    monkeypatch.setattr(
        health.psutil,
        "sensors_temperatures",
        lambda: {"cpu_thermal": [SimpleNamespace(current=55.0)]},
        raising=False,
    )
    assert health._read_temperature() == 55.0


def test_read_temperature_returns_none_when_unavailable(monkeypatch) -> None:
    class _FakePath:
        def __init__(self, p: str) -> None:
            pass

        def exists(self) -> bool:
            return False

    monkeypatch.setattr(health, "Path", _FakePath)
    monkeypatch.setattr(
        health.psutil, "sensors_temperatures", lambda: {}, raising=False
    )
    assert health._read_temperature() is None


# ---------------------------------------------------------------------------
# get_system_health
# ---------------------------------------------------------------------------

def test_get_system_health_returns_expected_structure(monkeypatch) -> None:
    monkeypatch.setattr(health.psutil, "cpu_percent", lambda interval=None: 25.0)
    monkeypatch.setattr(health.psutil, "cpu_count", lambda: 4)
    monkeypatch.setattr(
        health.psutil, "cpu_freq", lambda: SimpleNamespace(current=1500.0)
    )
    monkeypatch.setattr(
        health.psutil,
        "virtual_memory",
        lambda: SimpleNamespace(
            total=4 * 1024**3,
            used=1 * 1024**3,
            available=3 * 1024**3,
            percent=25.0,
        ),
    )
    monkeypatch.setattr(
        health.psutil,
        "swap_memory",
        lambda: SimpleNamespace(total=0, used=0, percent=0.0),
    )
    monkeypatch.setattr(
        health.psutil,
        "disk_usage",
        lambda p: SimpleNamespace(
            total=256 * 1024**3,
            used=64 * 1024**3,
            free=192 * 1024**3,
            percent=25.0,
        ),
    )
    monkeypatch.setattr(health.psutil, "boot_time", lambda: 0.0)
    monkeypatch.setattr(health, "_read_temperature", lambda: 52.3)

    class _FakePath:
        def __init__(self, p: str) -> None:
            self._p = p

        def exists(self) -> bool:
            return False

        def __str__(self) -> str:
            return self._p

    monkeypatch.setattr(health, "Path", _FakePath)

    result = health.get_system_health()

    assert result["cpu"]["percent"] == 25.0
    assert result["cpu"]["count"] == 4
    assert result["cpu"]["frequency_mhz"] == 1500.0
    assert result["memory"]["total_mb"] == round(4 * 1024, 1)
    assert result["memory"]["used_mb"] == round(1 * 1024, 1)
    assert result["memory"]["percent"] == 25.0
    assert result["disk"]["total_gb"] == 256.0
    assert result["disk"]["used_gb"] == 64.0
    assert result["disk"]["free_gb"] == 192.0
    assert result["disk"]["percent"] == 25.0
    assert result["temperature_c"] == 52.3
    assert result["uptime_seconds"] >= 0


def test_get_system_health_handles_no_cpu_freq(monkeypatch) -> None:
    monkeypatch.setattr(health.psutil, "cpu_percent", lambda interval=None: 10.0)
    monkeypatch.setattr(health.psutil, "cpu_count", lambda: 4)
    monkeypatch.setattr(health.psutil, "cpu_freq", lambda: None)
    monkeypatch.setattr(
        health.psutil,
        "virtual_memory",
        lambda: SimpleNamespace(
            total=2 * 1024**3, used=1 * 1024**3, available=1 * 1024**3, percent=50.0
        ),
    )
    monkeypatch.setattr(
        health.psutil,
        "swap_memory",
        lambda: SimpleNamespace(total=0, used=0, percent=0.0),
    )
    monkeypatch.setattr(
        health.psutil,
        "disk_usage",
        lambda p: SimpleNamespace(
            total=32 * 1024**3, used=8 * 1024**3, free=24 * 1024**3, percent=25.0
        ),
    )
    monkeypatch.setattr(health.psutil, "boot_time", lambda: 0.0)
    monkeypatch.setattr(health, "_read_temperature", lambda: None)

    class _FakePath:
        def __init__(self, p: str) -> None:
            pass

        def exists(self) -> bool:
            return False

        def __str__(self) -> str:
            return "/"

    monkeypatch.setattr(health, "Path", _FakePath)

    result = health.get_system_health()
    assert result["cpu"]["frequency_mhz"] is None
    assert result["temperature_c"] is None


# ---------------------------------------------------------------------------
# get_docker_services
# ---------------------------------------------------------------------------

def test_get_docker_services_returns_error_entry_when_docker_not_installed(
    monkeypatch,
) -> None:
    import builtins
    real_import = builtins.__import__

    def _no_docker(name, *args, **kwargs):
        if name == "docker":
            raise ImportError("No module named 'docker'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_docker)

    result = health.get_docker_services()
    assert len(result) == 1
    assert result[0]["status"] == "unavailable"


def test_get_docker_services_returns_error_entry_on_exception(monkeypatch) -> None:
    import builtins
    real_import = builtins.__import__

    class _FakeDocker:
        @staticmethod
        def from_env():
            raise RuntimeError("socket not found")

    class _FakeDockerModule:
        from_env = _FakeDocker.from_env

    def _patched_import(name, *args, **kwargs):
        if name == "docker":
            return _FakeDockerModule
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _patched_import)

    result = health.get_docker_services()
    assert len(result) == 1
    assert result[0]["state"] == "error"


# ---------------------------------------------------------------------------
# get_pmic_readings — error paths
# ---------------------------------------------------------------------------

def test_get_pmic_readings_parses_voltage_current_and_flags(monkeypatch) -> None:
    output = "\n".join(
        [
            "EXT5V_V volt(0)=5.01000000V",
            "EXT5V_A current(1)=0.75000000A",
            "VDD_CORE_V volt(2)=0.90000000V",
            "VDD_CORE_A current(3)=1.25000000A",
        ]
    )

    monkeypatch.setattr(
        health.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout=output),
    )

    data = health.get_pmic_readings()

    assert data["error"] is None
    assert data["ext5v_v"] == 5.01
    assert data["under_voltage"] is False
    assert data["total_power_w"] == 4.883
    assert data["rails"] == [
        {"name": "EXT5V", "voltage_v": 5.01, "current_a": 0.75, "power_w": 3.7575},
        {"name": "VDD_CORE", "voltage_v": 0.9, "current_a": 1.25, "power_w": 1.125},
    ]


def test_get_pmic_readings_sets_under_voltage_flag(monkeypatch) -> None:
    output = "EXT5V_V volt(0)=4.60000000V\nEXT5V_A current(1)=0.50000000A\n"
    monkeypatch.setattr(
        health.subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout=output)
    )
    data = health.get_pmic_readings()
    assert data["under_voltage"] is True


def test_get_pmic_readings_returns_error_when_vcgencmd_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        health.subprocess,
        "run",
        lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError("vcgencmd")),
    )
    data = health.get_pmic_readings()
    assert "vcgencmd not found" in data["error"]
    assert data["rails"] == []


def test_get_pmic_readings_returns_error_on_timeout(monkeypatch) -> None:
    def _raise(*a, **kw):
        raise health.subprocess.TimeoutExpired(cmd="vcgencmd", timeout=2)

    monkeypatch.setattr(health.subprocess, "run", _raise)
    data = health.get_pmic_readings()
    assert "timed out" in data["error"]
    assert data["rails"] == []


def test_get_pmic_readings_returns_error_on_empty_output(monkeypatch) -> None:
    monkeypatch.setattr(
        health.subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout="")
    )
    data = health.get_pmic_readings()
    assert data["error"] is not None
    assert data["rails"] == []


# ---------------------------------------------------------------------------
# get_power_status — error paths
# ---------------------------------------------------------------------------

def test_get_power_status_decodes_bit_flags(monkeypatch) -> None:
    monkeypatch.setattr(
        health.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout="throttled=0x50005\n"),
    )

    data = health.get_power_status()

    assert data["throttled_raw"] == "0x50005"
    assert data["under_voltage_now"] is True
    assert data["freq_capped_now"] is False
    assert data["throttled_now"] is True
    assert data["under_voltage_occurred"] is True
    assert data["freq_capping_occurred"] is False
    assert data["throttling_occurred"] is True
    assert data["healthy"] is False
    assert data["error"] is None


def test_get_power_status_healthy_when_zero(monkeypatch) -> None:
    monkeypatch.setattr(
        health.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(stdout="throttled=0x0\n"),
    )
    data = health.get_power_status()
    assert data["healthy"] is True
    assert data["under_voltage_now"] is False


def test_get_power_status_returns_error_when_vcgencmd_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        health.subprocess,
        "run",
        lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError("vcgencmd")),
    )
    data = health.get_power_status()
    assert data["healthy"] is None
    assert data["error"] is not None


def test_get_power_status_returns_error_on_timeout(monkeypatch) -> None:
    def _raise(*a, **kw):
        raise health.subprocess.TimeoutExpired(cmd="vcgencmd", timeout=2)

    monkeypatch.setattr(health.subprocess, "run", _raise)
    data = health.get_power_status()
    assert data["healthy"] is None
    assert "timed out" in data["error"]


def test_get_power_status_returns_error_on_unexpected_output(monkeypatch) -> None:
    monkeypatch.setattr(
        health.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(stdout="not_throttled_format\n"),
    )
    data = health.get_power_status()
    assert data["healthy"] is None
    assert data["error"] is not None
