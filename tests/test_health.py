from __future__ import annotations

from types import SimpleNamespace

from src import health


def test_fmt_ports_formats_bound_and_exposed_ports() -> None:
    ports = {
        "8000/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8000"}],
        "9090/tcp": None,
    }

    assert health._fmt_ports(ports) == "8000→8000/tcp, 9090/tcp"


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
