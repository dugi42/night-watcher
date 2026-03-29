"""OpenTelemetry SDK setup for metrics and distributed traces.

Initialises a MeterProvider backed by an OTLP HTTP exporter (targeting the
``otel-collector`` docker-compose service).  If the collector is unreachable
at startup the provider silently falls back to no-op — the application
continues running without metrics export.

Usage
-----
    from src.telemetry import setup_telemetry
    app_metrics = setup_telemetry()
    app_metrics.frames_processed.add(1)
"""
from __future__ import annotations

import logging
import os
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
        {"service.name": _SERVICE_NAME, "service.version": "0.2.0"}
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
