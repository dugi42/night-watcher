#!/usr/bin/env python3
"""Night Watcher — long-term health data analysis and README report generator.

Queries Prometheus for historical health metrics, computes summary statistics,
detects notable events (under-voltage, high temperature, CPU throttling), and
injects a structured report into README.md between the markers:

    <!-- HEALTH-REPORT-START -->
    <!-- HEALTH-REPORT-END -->

If the markers are absent the section is appended at the end of the file.

Requirements
------------
The ``requests`` package must be installed (already a client dependency):

    pip install requests          # or: uv sync --group client

Usage
-----
    # Default: query http://raspberrypi.local:9090, last 7 days, write README.md
    python scripts/analyze_health.py

    # Custom Prometheus and window
    python scripts/analyze_health.py --prometheus http://192.168.1.50:9090 --days 3

    # Print report to stdout only (do not modify README)
    python scripts/analyze_health.py --no-write

    # Write to a different file
    python scripts/analyze_health.py --output /tmp/report.md
"""
from __future__ import annotations

import argparse
import math
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit(
        "Error: 'requests' is required.\n"
        "Install it with:  pip install requests\n"
        "                  uv sync --group client"
    )

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROMETHEUS_DEFAULT = "http://raspberrypi.local:9090"
README_DEFAULT = Path(__file__).parent.parent / "README.md"
REPORT_START = "<!-- HEALTH-REPORT-START -->"
REPORT_END = "<!-- HEALTH-REPORT-END -->"

# Metric name → (prometheus_query, display_label, unit_suffix, decimal_places)
# Queries use the namespace applied by the otel-collector prometheus exporter.
METRICS: dict[str, tuple[str, str, str, int]] = {
    "cpu":       ("night_watcher_system_cpu_percent",          "CPU utilization",       "%",  1),
    "memory":    ("night_watcher_system_memory_percent",       "RAM utilization",       "%",  1),
    "disk":      ("night_watcher_system_disk_percent",         "Disk utilization",      "%",  1),
    "temp":      ("night_watcher_system_temperature_c",        "CPU temperature",       "°C", 1),
    "ext5v":     ("night_watcher_pmic_ext5v_v",                "Input voltage (EXT5V)", "V",  3),
    "power":     ("night_watcher_pmic_total_power_w",          "System power",          "W",  2),
    "core_v":    ('night_watcher_pmic_rail_voltage_v{rail="VDD_CORE"}', "CPU core voltage", "V", 3),
    "core_a":    ('night_watcher_pmic_rail_current_a{rail="VDD_CORE"}', "CPU core current", "A", 3),
}


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def query_range(
    prom_url: str,
    query: str,
    start: int,
    end: int,
    step: int = 60,
) -> list[float]:
    """Query Prometheus range API and return a flat list of float values.

    Returns an empty list on any error so callers can handle missing data
    gracefully without try/except at the call site.
    """
    try:
        resp = requests.get(
            f"{prom_url}/api/v1/query_range",
            params={"query": query, "start": start, "end": end, "step": step},
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "success":
            print(f"  Warning: Prometheus returned status={data.get('status')!r} for {query!r}",
                  file=sys.stderr)
            return []
        results = data.get("data", {}).get("result", [])
        if not results:
            return []
        # Take the first time series (handles label-selector queries returning one series)
        return [float(v[1]) for v in results[0]["values"] if v[1] not in ("NaN", "+Inf", "-Inf")]
    except requests.ConnectionError:
        print(f"  Warning: could not connect to Prometheus at {prom_url}", file=sys.stderr)
        return []
    except Exception as exc:
        print(f"  Warning: query failed for {query!r}: {exc}", file=sys.stderr)
        return []


# ---------------------------------------------------------------------------
# Statistics helpers
# ---------------------------------------------------------------------------

def percentile(data: list[float], p: float) -> float:
    """Compute the *p*-th percentile of *data* (0–100) via linear interpolation."""
    if not data:
        return float("nan")
    s = sorted(data)
    k = (len(s) - 1) * p / 100.0
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return s[lo]
    return s[lo] * (hi - k) + s[hi] * (k - lo)


def compute_stats(values: list[float]) -> dict:
    """Return a dict of summary statistics, or ``{"n": 0}`` when *values* is empty."""
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "min": min(values),
        "max": max(values),
        "mean": statistics.mean(values),
        "median": statistics.median(values),
        "p95": percentile(values, 95),
        "p99": percentile(values, 99),
        "stdev": statistics.stdev(values) if len(values) > 1 else 0.0,
    }


def fmt(v: float, unit: str, decimals: int = 1) -> str:
    """Format a float value with unit; returns ``"N/A"`` for NaN."""
    if math.isnan(v):
        return "N/A"
    return f"{v:.{decimals}f} {unit}".strip()


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def generate_report(prom_url: str, days: float) -> str:
    """Query Prometheus and return the full markdown report as a string."""
    now = int(datetime.now(tz=timezone.utc).timestamp())
    start = int(now - days * 86400)
    # Aim for ≈2 000 data points; coarsen step for long windows
    step = max(30, int(days * 86400 / 2000))

    print(f"Querying Prometheus at {prom_url}")
    print(f"  Window: {days:.1f} day(s)  |  step: {step} s  |  ~{int(days * 86400 / step)} points per metric")

    all_values: dict[str, list[float]] = {}
    all_stats: dict[str, dict] = {}
    for key, (query, label, _unit, _dec) in METRICS.items():
        print(f"  Fetching {label} …")
        v = query_range(prom_url, query, start, now, step)
        all_values[key] = v
        all_stats[key] = compute_stats(v)

    ts_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    day_str = f"{days:.0f} day{'s' if days != 1 else ''}"

    lines: list[str] = [
        f"<!-- Generated by scripts/analyze_health.py on {ts_str} -->",
        "",
        f"**Generated:** {ts_str} &nbsp;·&nbsp; **Data window:** {day_str}",
        "",
    ]

    # --- Resource summary table ---
    lines += [
        "### Resource Summary",
        "",
        "| Metric | Min | Avg | Max | P95 |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    table_rows = [
        ("cpu",    "CPU utilization",       "%",  1),
        ("memory", "RAM utilization",       "%",  1),
        ("disk",   "Disk utilization",      "%",  1),
        ("temp",   "CPU temperature",       "°C", 1),
        ("ext5v",  "Input voltage (EXT5V)", "V",  3),
        ("power",  "System power",          "W",  2),
        ("core_a", "CPU core current",      "A",  3),
    ]
    for key, label, unit, dec in table_rows:
        s = all_stats.get(key, {})
        if s.get("n", 0) == 0:
            lines.append(f"| {label} | — | — | — | — |")
        else:
            lines.append(
                f"| {label} "
                f"| {s['min']:.{dec}f} {unit} "
                f"| {s['mean']:.{dec}f} {unit} "
                f"| {s['max']:.{dec}f} {unit} "
                f"| {s['p95']:.{dec}f} {unit} |"
            )

    # --- Notable events ---
    lines += ["", "### Notable Events", ""]

    # Under-voltage
    v5 = all_values.get("ext5v", [])
    if v5:
        uv_n = sum(1 for v in v5 if v < 4.75)
        uv_pct = 100.0 * uv_n / len(v5)
        min_v = min(v5)
        if uv_n == 0:
            lines.append(f"- ✅ **No under-voltage events** — EXT5V stayed above 4.75 V (min: {min_v:.3f} V)")
        else:
            lines.append(
                f"- ⚠️ **Under-voltage detected:** {uv_n} samples ({uv_pct:.1f}% of time) "
                f"below 4.75 V (min: {min_v:.3f} V) — check USB-C power supply"
            )
    else:
        lines.append("- ℹ️ Input voltage data not available (PMIC metrics not yet collected)")

    # High temperature
    temps = all_values.get("temp", [])
    if temps:
        hot_n = sum(1 for t in temps if t >= 80.0)
        warm_n = sum(1 for t in temps if 70.0 <= t < 80.0)
        max_t = max(temps)
        if hot_n == 0 and warm_n == 0:
            lines.append(f"- ✅ **Temperature healthy** — max {max_t:.1f}°C, always below 70°C")
        elif hot_n == 0:
            lines.append(
                f"- 🟡 **Temperature occasionally warm:** {warm_n} samples ≥ 70°C "
                f"(max {max_t:.1f}°C) — monitor if deployed in direct sun"
            )
        else:
            hot_pct = 100.0 * hot_n / len(temps)
            lines.append(
                f"- 🔴 **High temperature:** {hot_n} samples ≥ 80°C ({hot_pct:.1f}% of time), "
                f"max {max_t:.1f}°C — check enclosure ventilation"
            )
    else:
        lines.append("- ℹ️ Temperature data not available")

    # CPU load
    cpu_vals = all_values.get("cpu", [])
    if cpu_vals:
        s = all_stats["cpu"]
        heavy_n = sum(1 for c in cpu_vals if c >= 90.0)
        heavy_pct = 100.0 * heavy_n / len(cpu_vals)
        if heavy_pct < 1.0:
            lines.append(
                f"- ✅ **CPU load healthy** — avg {s['mean']:.1f}%, "
                f"rarely exceeds 90% ({heavy_pct:.1f}% of samples)"
            )
        else:
            lines.append(
                f"- 🟡 **CPU load elevated:** avg {s['mean']:.1f}%, "
                f"spent {heavy_pct:.1f}% of time above 90% — "
                f"consider reducing detection frame rate if needed"
            )
    else:
        lines.append("- ℹ️ CPU utilization data not available")

    # Memory
    mem_vals = all_values.get("memory", [])
    if mem_vals:
        s = all_stats["memory"]
        if s["max"] < 80.0:
            lines.append(
                f"- ✅ **RAM healthy** — avg {s['mean']:.1f}%, peak {s['max']:.1f}%"
            )
        elif s["p95"] < 90.0:
            lines.append(
                f"- 🟡 **RAM usage elevated:** avg {s['mean']:.1f}%, peak {s['max']:.1f}% "
                f"— monitor for OOM risk"
            )
        else:
            lines.append(
                f"- 🔴 **RAM critically high:** P95 {s['p95']:.1f}%, peak {s['max']:.1f}% "
                f"— risk of OOM kills"
            )
    else:
        lines.append("- ℹ️ Memory data not available")

    # --- Power consumption section ---
    lines += ["", "### Power Consumption", ""]
    pwr_vals = all_values.get("power", [])
    if pwr_vals:
        s = all_stats["power"]
        pct_psu = 100.0 * s["max"] / 27.0
        lines.append(
            f"Average system power: **{s['mean']:.1f} W** &nbsp;·&nbsp; "
            f"Peak: **{s['max']:.1f} W** &nbsp;·&nbsp; "
            f"P95: **{s['p95']:.1f} W**"
        )
        lines.append("")
        if s["max"] < 18.0:
            lines.append(
                f"Peak draw ({s['max']:.1f} W) is {pct_psu:.0f}% of the 27 W PSU "
                f"— comfortable headroom for the recommended Raspberry Pi 27 W USB-C PSU."
            )
        else:
            lines.append(
                f"Peak draw ({s['max']:.1f} W) is {pct_psu:.0f}% of the 27 W PSU "
                f"— approaching the limit; verify you are using the official 27 W PSU."
            )

        core_a_vals = all_values.get("core_a", [])
        if core_a_vals:
            sa = all_stats["core_a"]
            lines.append("")
            lines.append(
                f"CPU core current: avg **{sa['mean']:.3f} A**, "
                f"peak **{sa['max']:.3f} A**, "
                f"P95 **{sa['p95']:.3f} A**"
            )
    else:
        lines.append("Power data not available (PMIC metrics not yet collected).")

    # --- Thermal section ---
    lines += ["", "### Thermal Management", ""]
    if temps:
        s = all_stats["temp"]
        lines.append(
            f"Average temperature: **{s['mean']:.1f}°C** &nbsp;·&nbsp; "
            f"Peak: **{s['max']:.1f}°C** &nbsp;·&nbsp; "
            f"P95: **{s['p95']:.1f}°C**"
        )
        lines.append("")
        if s["max"] < 60.0:
            lines.append(
                "Temperature stays well below the 80°C soft-throttle limit. "
                "Passive cooling is sufficient for this deployment."
            )
        elif s["p95"] < 70.0:
            lines.append(
                "Temperature is within acceptable range. "
                "Occasional spikes under YOLO inference load are normal — the heatsink is working."
            )
        elif s["p95"] < 80.0:
            lines.append(
                "Temperature is frequently elevated. "
                "Consider improving enclosure ventilation or adding a small fan."
            )
        else:
            lines.append(
                "Temperature is regularly near or above the soft-throttle threshold (80°C). "
                "CPU frequency is likely being capped. "
                "Improve enclosure airflow or add active cooling."
            )
    else:
        lines.append("Temperature data not available.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# README injection
# ---------------------------------------------------------------------------

def inject_report(readme_path: Path, report: str) -> None:
    """Replace the health report section in *readme_path* with *report*.

    The content between ``<!-- HEALTH-REPORT-START -->`` and
    ``<!-- HEALTH-REPORT-END -->`` is replaced in-place.  If the markers are
    absent the section is appended at the end of the file under a new
    ``## Long-term Health Report`` heading.
    """
    content = readme_path.read_text(encoding="utf-8")
    section = f"{REPORT_START}\n{report}\n{REPORT_END}"

    if REPORT_START in content and REPORT_END in content:
        s = content.index(REPORT_START)
        e = content.index(REPORT_END) + len(REPORT_END)
        new_content = content[:s] + section + content[e:]
    else:
        new_content = (
            content.rstrip("\n")
            + "\n\n---\n\n## Long-term Health Report\n\n"
            + section
            + "\n"
        )

    readme_path.write_text(new_content, encoding="utf-8")
    print(f"Report written to {readme_path}")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze Night Watcher health metrics from Prometheus and update README",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--prometheus",
        default=PROMETHEUS_DEFAULT,
        metavar="URL",
        help="Prometheus base URL",
    )
    parser.add_argument(
        "--days",
        type=float,
        default=7.0,
        help="Analysis window in days",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=README_DEFAULT,
        metavar="FILE",
        help="README file to update",
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="Print the report to stdout only; do not modify any files",
    )
    args = parser.parse_args()

    report = generate_report(args.prometheus, args.days)

    if args.no_write:
        print("\n" + report)
        return

    inject_report(args.output, report)
    print("Done.")


if __name__ == "__main__":
    main()
