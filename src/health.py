"""System health and Docker service monitoring.

Provides functions to collect Raspberry Pi system metrics (CPU, memory, disk,
temperature) via psutil, enumerate running Docker containers via the Docker
Python SDK (requires /var/run/docker.sock mounted read-only), and read
power/throttle status via vcgencmd (requires /dev/vcio device access).
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

import psutil


def get_system_health() -> dict[str, Any]:
    """Return current system health metrics.

    Returns
    -------
    dict[str, Any]
        Nested dict with keys: ``cpu``, ``memory``, ``swap``, ``disk``,
        ``temperature_c``, ``uptime_seconds``.
    """
    cpu_percent = psutil.cpu_percent(interval=0.2)
    cpu_count = psutil.cpu_count()
    cpu_freq = psutil.cpu_freq()

    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()

    assets_path = Path("/assets")
    disk = psutil.disk_usage(str(assets_path) if assets_path.exists() else "/")

    uptime_s = time.time() - psutil.boot_time()
    temperature = _read_temperature()

    return {
        "cpu": {
            "percent": round(cpu_percent, 1),
            "count": cpu_count,
            "frequency_mhz": round(cpu_freq.current, 1) if cpu_freq else None,
        },
        "memory": {
            "total_mb": round(mem.total / 1024 ** 2, 1),
            "used_mb": round(mem.used / 1024 ** 2, 1),
            "available_mb": round(mem.available / 1024 ** 2, 1),
            "percent": round(mem.percent, 1),
        },
        "swap": {
            "total_mb": round(swap.total / 1024 ** 2, 1),
            "used_mb": round(swap.used / 1024 ** 2, 1),
            "percent": round(swap.percent, 1),
        },
        "disk": {
            "total_gb": round(disk.total / 1024 ** 3, 2),
            "used_gb": round(disk.used / 1024 ** 3, 2),
            "free_gb": round(disk.free / 1024 ** 3, 2),
            "percent": round(disk.percent, 1),
        },
        "temperature_c": temperature,
        "uptime_seconds": round(uptime_s),
    }


def _read_temperature() -> float | None:
    """Read CPU temperature from the Linux thermal zone or psutil."""
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
    except (AttributeError, Exception):
        pass
    return None


def get_docker_services() -> list[dict[str, str]]:
    """Return a list of all Docker containers (requires /var/run/docker.sock).

    Returns
    -------
    list[dict[str, str]]
        Each entry has keys: ``id``, ``name``, ``image``, ``status``,
        ``state``, ``ports``, ``created``.  Returns a single error entry
        when the Docker socket is unavailable.
    """
    try:
        import docker  # type: ignore[import]

        client = docker.from_env()
        result: list[dict[str, str]] = []
        for c in client.containers.list(all=True):
            result.append(
                {
                    "id": c.short_id,
                    "name": c.name,
                    "image": c.image.tags[0] if c.image.tags else c.image.short_id,
                    "status": c.status,
                    "state": c.attrs.get("State", {}).get("Status", c.status),
                    "ports": _fmt_ports(c.ports),
                    "created": c.attrs.get("Created", "")[:19].replace("T", " "),
                }
            )
        return result
    except ImportError:
        return [
            {
                "id": "",
                "name": "docker SDK not installed",
                "image": "",
                "status": "unavailable",
                "state": "unavailable",
                "ports": "",
                "created": "",
            }
        ]
    except Exception as exc:
        return [
            {
                "id": "",
                "name": str(exc),
                "image": "",
                "status": "error",
                "state": "error",
                "ports": "",
                "created": "",
            }
        ]


def get_pmic_readings() -> dict[str, Any]:
    """Return live voltage and current readings from the Raspberry Pi 5 PMIC.

    Uses ``vcgencmd pmic_read_adc`` which queries all ADC channels on the
    MXL7704 PMIC.  Returns the most diagnostically useful rails plus a
    computed total system power estimate.

    Key rails
    ---------
    ``ext5v_v``
        Input voltage from the USB-C power supply.  Healthy range: 4.8–5.2 V.
        Values below ~4.75 V indicate a weak power supply.
    ``vdd_core_v`` / ``vdd_core_a``
        CPU core voltage and current — spikes under YOLO inference load.
    ``3v3_sys_v``
        3.3 V system rail used by most peripherals.

    Returns
    -------
    dict[str, Any]
        Keys: ``rails`` (list of dicts with ``name``, ``voltage_v``,
        ``current_a``), ``total_power_w`` (float), ``ext5v_v`` (float | None),
        ``under_voltage`` (bool), ``error`` (str | None).
    """
    try:
        result = subprocess.run(
            ["vcgencmd", "pmic_read_adc"],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
        lines = result.stdout.strip().splitlines()
        if not lines:
            return {"error": "vcgencmd pmic_read_adc returned no output", "rails": []}

        voltages: dict[str, float] = {}
        currents: dict[str, float] = {}

        for line in lines:
            # Format: "  VDD_CORE_A current(7)=1.93781000A"
            # or:     "  VDD_CORE_V volt(15)=0.87650700V"
            line = line.strip()
            if "=" not in line:
                continue
            name_part, val_part = line.split("=", 1)
            name_part = name_part.strip()
            val_str = val_part.strip().rstrip("AV").strip()
            try:
                val = float(val_str)
            except ValueError:
                continue
            # Split "VDD_CORE_A current(7)" → rail name and type
            parts = name_part.split()
            if not parts:
                continue
            rail_full = parts[0]  # e.g. "VDD_CORE_A" or "VDD_CORE_V"
            if rail_full.endswith("_A"):
                rail = rail_full[:-2]
                currents[rail] = val
            elif rail_full.endswith("_V"):
                rail = rail_full[:-2]
                voltages[rail] = val

        all_rails = sorted(set(voltages) | set(currents))
        rails = [
            {
                "name": r,
                "voltage_v": round(voltages.get(r, 0.0), 4),
                "current_a": round(currents.get(r, 0.0), 4),
                "power_w": round(voltages.get(r, 0.0) * currents.get(r, 0.0), 4),
            }
            for r in all_rails
        ]

        # Total estimated power from all rails that have both V and I
        total_power = sum(
            voltages[r] * currents[r]
            for r in all_rails
            if r in voltages and r in currents
        )

        ext5v = voltages.get("EXT5V")
        # Under-voltage if input rail is below 4.75 V (Pi 5 spec is 5.0 V ± 5%)
        under_voltage = ext5v is not None and ext5v < 4.75

        return {
            "rails": rails,
            "total_power_w": round(total_power, 3),
            "ext5v_v": round(ext5v, 4) if ext5v is not None else None,
            "under_voltage": under_voltage,
            "error": None,
        }
    except FileNotFoundError:
        return {"error": "vcgencmd not found", "rails": []}
    except subprocess.TimeoutExpired:
        return {"error": "vcgencmd timed out", "rails": []}
    except Exception as exc:
        return {"error": str(exc), "rails": []}


def get_power_status() -> dict[str, Any]:
    """Return Raspberry Pi power and throttle status via ``vcgencmd get_throttled``.

    Requires the ``vcgencmd`` binary (bind-mounted from the host) and the
    ``/dev/vcio`` device to be accessible inside the container.

    The returned bitmask flags distinguish *current* state (bits 0–3) from
    *has-occurred-since-boot* state (bits 16–19).  A healthy, well-powered Pi
    returns ``throttled=0x0``.

    Returns
    -------
    dict[str, Any]
        Keys:

        ``throttled_raw`` (str)
            Raw hex value from vcgencmd, e.g. ``"0x50005"``.
        ``under_voltage_now`` (bool)
            Voltage currently below 4.63 V.
        ``freq_capped_now`` (bool)
            ARM frequency currently capped due to thermal or power limit.
        ``throttled_now`` (bool)
            CPU currently throttled.
        ``soft_temp_limit_now`` (bool)
            Soft temperature limit currently active.
        ``under_voltage_occurred`` (bool)
            Under-voltage detected at any point since last boot.
        ``freq_capping_occurred`` (bool)
            Frequency capping occurred since last boot.
        ``throttling_occurred`` (bool)
            CPU throttling occurred since last boot.
        ``soft_temp_limit_occurred`` (bool)
            Soft temperature limit was hit since last boot.
        ``healthy`` (bool | None)
            ``True`` if throttled value is 0x0, ``False`` if any flag is set,
            ``None`` if vcgencmd is unavailable.
        ``error`` (str | None)
            Error message when vcgencmd cannot be called.
    """
    try:
        result = subprocess.run(
            ["vcgencmd", "get_throttled"],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
        raw = result.stdout.strip()  # e.g. "throttled=0x50005"
        if "=" not in raw:
            return {"error": f"Unexpected vcgencmd output: {raw!r}", "healthy": None}

        val_str = raw.split("=", 1)[1]
        throttled = int(val_str, 16)

        return {
            "throttled_raw": hex(throttled),
            "under_voltage_now": bool(throttled & 0x1),
            "freq_capped_now": bool(throttled & 0x2),
            "throttled_now": bool(throttled & 0x4),
            "soft_temp_limit_now": bool(throttled & 0x8),
            "under_voltage_occurred": bool(throttled & 0x10000),
            "freq_capping_occurred": bool(throttled & 0x20000),
            "throttling_occurred": bool(throttled & 0x40000),
            "soft_temp_limit_occurred": bool(throttled & 0x80000),
            "healthy": throttled == 0,
            "error": None,
        }
    except FileNotFoundError:
        return {
            "error": "vcgencmd not found — mount /usr/bin/vcgencmd and /dev/vcio",
            "healthy": None,
        }
    except subprocess.TimeoutExpired:
        return {"error": "vcgencmd timed out", "healthy": None}
    except Exception as exc:
        return {"error": str(exc), "healthy": None}


def _fmt_ports(ports: dict[str, Any]) -> str:
    """Format Docker port bindings as a compact human-readable string."""
    if not ports:
        return ""
    parts: list[str] = []
    for container_port, bindings in ports.items():
        if bindings:
            for b in bindings:
                parts.append(f"{b['HostPort']}→{container_port}")
        else:
            parts.append(container_port)
    return ", ".join(parts)
