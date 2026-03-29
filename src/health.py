"""System health and Docker service monitoring.

Provides functions to collect Raspberry Pi system metrics (CPU, memory, disk,
temperature) via psutil, and to enumerate running Docker containers via the
Docker Python SDK (requires /var/run/docker.sock mounted read-only).
"""
from __future__ import annotations

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
