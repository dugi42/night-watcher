"""OpenTelemetry SDK setup for metrics and distributed traces.

Initialises a MeterProvider backed by an OTLP HTTP exporter (targeting the
``otel-collector`` docker-compose service).  If the collector is unreachable
at startup the provider silently falls back to no-op — the application
continues running without metrics export.

Usage
-----
    from src.telemetry import setup_telemetry, setup_health_telemetry
    app_metrics = setup_telemetry()
    setup_health_telemetry()          # register system + PMIC observable gauges
    app_metrics.frames_processed.add(1)
"""
from __future__ import annotations

import logging
import os
import time as _time
from dataclasses import dataclass

from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

_OTEL_ENDPOINT = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4318")
_SERVICE_NAME = "night-watcher"

logger = logging.getLogger("night_watcher.telemetry")


@dataclass
class AppMetrics:
    """OpenTelemetry metric instruments for the application."""

    frames_processed: metrics.Counter
    frame_processing_ms: metrics.Histogram
    detections_total: metrics.Counter
    sessions_started: metrics.Counter


def setup_telemetry() -> AppMetrics:
    """Initialise OTel SDK and return instrumented metric objects.

    Returns
    -------
    AppMetrics
        Dataclass holding all metric instruments.  All instruments are
        no-ops when the exporter is unavailable.
    """
    resource = Resource.create(
        {"service.name": _SERVICE_NAME, "service.version": "1.0.0"}
    )

    # --- Metrics provider ---
    try:
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
            OTLPMetricExporter,
        )

        exporter = OTLPMetricExporter(endpoint=f"{_OTEL_ENDPOINT}/v1/metrics")
        reader = PeriodicExportingMetricReader(exporter, export_interval_millis=15_000)
        meter_provider: MeterProvider = MeterProvider(
            resource=resource, metric_readers=[reader]
        )
        logger.info("OTel metrics exporter → %s/v1/metrics", _OTEL_ENDPOINT)
    except Exception as exc:
        logger.warning("OTel metrics exporter unavailable (%s) — using no-op", exc)
        meter_provider = MeterProvider(resource=resource)

    metrics.set_meter_provider(meter_provider)

    # --- Trace provider ---
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )

        span_exporter = OTLPSpanExporter(endpoint=f"{_OTEL_ENDPOINT}/v1/traces")
        tracer_provider = TracerProvider(resource=resource)
        tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
        trace.set_tracer_provider(tracer_provider)
        logger.info("OTel trace exporter → %s/v1/traces", _OTEL_ENDPOINT)
    except Exception as exc:
        logger.warning("OTel trace exporter unavailable (%s)", exc)

    meter = metrics.get_meter(_SERVICE_NAME)
    return AppMetrics(
        frames_processed=meter.create_counter(
            "night_watcher.frames.processed",
            description="Total camera frames processed by the detection loop",
        ),
        frame_processing_ms=meter.create_histogram(
            "night_watcher.frames.processing_ms",
            description="YOLO inference + annotation latency per frame",
            unit="ms",
        ),
        detections_total=meter.create_counter(
            "night_watcher.detections.total",
            description="Object detections counted by class label",
        ),
        sessions_started=meter.create_counter(
            "night_watcher.sessions.started",
            description="Number of detection sessions started",
        ),
    )


def setup_health_telemetry() -> None:
    """Register system health and PMIC observable gauges against the active MeterProvider.

    Call this once after :func:`setup_telemetry` so the MeterProvider is already
    initialised.  Gauges are polled automatically by the
    ``PeriodicExportingMetricReader`` at the configured export interval (15 s).

    The callbacks read from a short-lived cache (TTL 12 s) so that multiple
    gauge instruments do not each invoke a separate ``psutil`` or ``vcgencmd``
    call within the same export cycle.

    Resulting Prometheus metric names (with otel-collector namespace ``night_watcher``):

    .. code-block:: text

        night_watcher_system_cpu_percent
        night_watcher_system_memory_percent
        night_watcher_system_disk_percent
        night_watcher_system_temperature_c
        night_watcher_system_cpu_frequency_mhz
        night_watcher_pmic_ext5v_v
        night_watcher_pmic_total_power_w
        night_watcher_pmic_rail_voltage_v{rail="<RAIL>"}
        night_watcher_pmic_rail_current_a{rail="<RAIL>"}
    """
    from opentelemetry.metrics import Observation

    meter = metrics.get_meter(_SERVICE_NAME)

    # -----------------------------------------------------------------------
    # Per-export-cycle cache — avoids redundant psutil / vcgencmd calls when
    # multiple gauge callbacks fire within the same 15-second export window.
    # -----------------------------------------------------------------------
    _CACHE_TTL = 12.0

    _sys_data: list[dict] = [{}]
    _sys_ts: list[float] = [0.0]
    _pmic_data: list[dict] = [{}]
    _pmic_ts: list[float] = [0.0]

    def _sys() -> dict:
        if _time.time() - _sys_ts[0] > _CACHE_TTL:
            try:
                from src.health import get_system_health  # noqa: PLC0415
                _sys_data[0] = get_system_health()
                _sys_ts[0] = _time.time()
            except Exception:
                pass
        return _sys_data[0]

    def _pmic() -> dict:
        if _time.time() - _pmic_ts[0] > _CACHE_TTL:
            try:
                from src.health import get_pmic_readings  # noqa: PLC0415
                _pmic_data[0] = get_pmic_readings()
                _pmic_ts[0] = _time.time()
            except Exception:
                pass
        return _pmic_data[0]

    # -----------------------------------------------------------------------
    # Observable gauge callbacks
    # -----------------------------------------------------------------------

    def _cb_cpu(options):
        d = _sys()
        if d.get("cpu", {}).get("percent") is not None:
            yield Observation(d["cpu"]["percent"])

    def _cb_memory(options):
        d = _sys()
        if d.get("memory", {}).get("percent") is not None:
            yield Observation(d["memory"]["percent"])

    def _cb_disk(options):
        d = _sys()
        if d.get("disk", {}).get("percent") is not None:
            yield Observation(d["disk"]["percent"])

    def _cb_temp(options):
        d = _sys()
        temp = d.get("temperature_c")
        if temp is not None:
            yield Observation(temp)

    def _cb_freq(options):
        d = _sys()
        freq = d.get("cpu", {}).get("frequency_mhz")
        if freq is not None:
            yield Observation(freq)

    def _cb_ext5v(options):
        d = _pmic()
        v = d.get("ext5v_v")
        if v is not None:
            yield Observation(v)

    def _cb_power(options):
        d = _pmic()
        p = d.get("total_power_w")
        if p is not None:
            yield Observation(p)

    def _cb_rail_v(options):
        d = _pmic()
        for rail in d.get("rails", []):
            yield Observation(rail["voltage_v"], {"rail": rail["name"]})

    def _cb_rail_a(options):
        d = _pmic()
        for rail in d.get("rails", []):
            yield Observation(rail["current_a"], {"rail": rail["name"]})

    # -----------------------------------------------------------------------
    # Register gauges
    # -----------------------------------------------------------------------
    meter.create_observable_gauge(
        "system.cpu.percent", callbacks=[_cb_cpu],
        description="CPU utilization", unit="%",
    )
    meter.create_observable_gauge(
        "system.memory.percent", callbacks=[_cb_memory],
        description="RAM utilization", unit="%",
    )
    meter.create_observable_gauge(
        "system.disk.percent", callbacks=[_cb_disk],
        description="Disk (assets) utilization", unit="%",
    )
    meter.create_observable_gauge(
        "system.temperature_c", callbacks=[_cb_temp],
        description="CPU temperature", unit="Cel",
    )
    meter.create_observable_gauge(
        "system.cpu.frequency_mhz", callbacks=[_cb_freq],
        description="CPU clock frequency", unit="MHz",
    )
    meter.create_observable_gauge(
        "pmic.ext5v_v", callbacks=[_cb_ext5v],
        description="USB-C power supply input voltage", unit="V",
    )
    meter.create_observable_gauge(
        "pmic.total_power_w", callbacks=[_cb_power],
        description="Total estimated system power across all PMIC rails", unit="W",
    )
    meter.create_observable_gauge(
        "pmic.rail.voltage_v", callbacks=[_cb_rail_v],
        description="PMIC rail voltage (labelled by rail name)", unit="V",
    )
    meter.create_observable_gauge(
        "pmic.rail.current_a", callbacks=[_cb_rail_a],
        description="PMIC rail current (labelled by rail name)", unit="A",
    )
    logger.info("Health observable gauges registered (system CPU/memory/disk/temp + PMIC rails)")
