from __future__ import annotations

import sys
import types

from src import telemetry
from src import __version__


class _FakeInstrument:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[tuple] = []

    def add(self, value, attributes=None) -> None:
        self.calls.append((value, attributes))

    def record(self, value) -> None:
        self.calls.append((value,))


class _FakeMeter:
    def __init__(self) -> None:
        self.observables: dict[str, list] = {}

    def create_counter(self, name: str, **_kwargs):
        return _FakeInstrument(name)

    def create_histogram(self, name: str, **_kwargs):
        return _FakeInstrument(name)

    def create_observable_gauge(self, name: str, callbacks: list, **_kwargs) -> None:
        self.observables[name] = callbacks


class _FakeMetricReader:
    def __init__(self, exporter, export_interval_millis: int) -> None:
        self.exporter = exporter
        self.export_interval_millis = export_interval_millis


class _FakeMeterProvider:
    def __init__(self, resource, metric_readers=None) -> None:
        self.resource = resource
        self.metric_readers = metric_readers or []


class _FakeTracerProvider:
    def __init__(self, resource) -> None:
        self.resource = resource
        self.processors: list[object] = []

    def add_span_processor(self, processor) -> None:
        self.processors.append(processor)


def test_setup_telemetry_creates_metric_and_trace_providers(monkeypatch) -> None:
    fake_meter = _FakeMeter()
    meter_providers: list[_FakeMeterProvider] = []
    trace_providers: list[_FakeTracerProvider] = []

    metric_module = types.ModuleType("metric_exporter")
    trace_module = types.ModuleType("trace_exporter")

    class _OTLPMetricExporter:
        def __init__(self, endpoint: str) -> None:
            self.endpoint = endpoint

    class _OTLPSpanExporter:
        def __init__(self, endpoint: str) -> None:
            self.endpoint = endpoint

    metric_module.OTLPMetricExporter = _OTLPMetricExporter
    trace_module.OTLPSpanExporter = _OTLPSpanExporter
    monkeypatch.setitem(sys.modules, "opentelemetry.exporter.otlp.proto.http.metric_exporter", metric_module)
    monkeypatch.setitem(sys.modules, "opentelemetry.exporter.otlp.proto.http.trace_exporter", trace_module)
    monkeypatch.setattr(telemetry, "PeriodicExportingMetricReader", _FakeMetricReader)
    monkeypatch.setattr(telemetry, "MeterProvider", _FakeMeterProvider)
    monkeypatch.setattr(telemetry, "TracerProvider", _FakeTracerProvider)
    monkeypatch.setattr(telemetry, "BatchSpanProcessor", lambda exporter: ("processor", exporter))
    monkeypatch.setattr(telemetry.Resource, "create", staticmethod(lambda attrs: attrs))
    monkeypatch.setattr(telemetry.metrics, "set_meter_provider", lambda provider: meter_providers.append(provider))
    monkeypatch.setattr(telemetry.metrics, "get_meter", lambda _name: fake_meter)
    monkeypatch.setattr(telemetry.trace, "set_tracer_provider", lambda provider: trace_providers.append(provider))

    app_metrics = telemetry.setup_telemetry()

    assert app_metrics.frames_processed.name == "frames.processed"
    assert app_metrics.frame_processing_ms.name == "frames.processing_ms"
    assert app_metrics.detections_total.name == "detections.total"
    assert app_metrics.sessions_started.name == "sessions.started"
    assert meter_providers[0].resource["service.version"] == __version__
    assert meter_providers[0].metric_readers[0].exporter.endpoint.endswith("/v1/metrics")
    assert trace_providers[0].processors[0][1].endpoint.endswith("/v1/traces")

