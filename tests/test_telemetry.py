from __future__ import annotations

import sys
import types

from src import telemetry


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

    assert app_metrics.frames_processed.name == "night_watcher.frames.processed"
    assert app_metrics.frame_processing_ms.name == "night_watcher.frames.processing_ms"
    assert app_metrics.detections_total.name == "night_watcher.detections.total"
    assert app_metrics.sessions_started.name == "night_watcher.sessions.started"
    assert meter_providers[0].resource["service.version"] == "1.0.0"
    assert meter_providers[0].metric_readers[0].exporter.endpoint.endswith("/v1/metrics")
    assert trace_providers[0].processors[0][1].endpoint.endswith("/v1/traces")


def test_setup_health_telemetry_registers_callbacks_and_uses_cache(monkeypatch) -> None:
    fake_meter = _FakeMeter()
    sys_calls = {"count": 0}
    pmic_calls = {"count": 0}

    monkeypatch.setattr(telemetry.metrics, "get_meter", lambda _name: fake_meter)
    monkeypatch.setattr(telemetry._time, "time", lambda: 100.0)

    import src.health as health

    def fake_system_health():
        sys_calls["count"] += 1
        return {
            "cpu": {"percent": 47.5, "frequency_mhz": 2400.0},
            "memory": {"percent": 31.0},
            "disk": {"percent": 12.5},
            "temperature_c": 64.2,
        }

    def fake_pmic():
        pmic_calls["count"] += 1
        return {
            "ext5v_v": 5.1,
            "total_power_w": 4.6,
            "rails": [
                {"name": "EXT5V", "voltage_v": 5.1, "current_a": 0.9},
                {"name": "CORE", "voltage_v": 0.9, "current_a": 1.2},
            ],
        }

    monkeypatch.setattr(health, "get_system_health", fake_system_health)
    monkeypatch.setattr(health, "get_pmic_readings", fake_pmic)

    telemetry.setup_health_telemetry()

    cpu_obs = list(fake_meter.observables["system.cpu.percent"][0](None))
    mem_obs = list(fake_meter.observables["system.memory.percent"][0](None))
    disk_obs = list(fake_meter.observables["system.disk.percent"][0](None))
    temp_obs = list(fake_meter.observables["system.temperature_c"][0](None))
    freq_obs = list(fake_meter.observables["system.cpu.frequency_mhz"][0](None))
    ext5v_obs = list(fake_meter.observables["pmic.ext5v_v"][0](None))
    power_obs = list(fake_meter.observables["pmic.total_power_w"][0](None))
    rail_v_obs = list(fake_meter.observables["pmic.rail.voltage_v"][0](None))
    rail_a_obs = list(fake_meter.observables["pmic.rail.current_a"][0](None))

    assert [obs.value for obs in cpu_obs] == [47.5]
    assert [obs.value for obs in mem_obs] == [31.0]
    assert [obs.value for obs in disk_obs] == [12.5]
    assert [obs.value for obs in temp_obs] == [64.2]
    assert [obs.value for obs in freq_obs] == [2400.0]
    assert [obs.value for obs in ext5v_obs] == [5.1]
    assert [obs.value for obs in power_obs] == [4.6]
    assert [(obs.value, obs.attributes["rail"]) for obs in rail_v_obs] == [(5.1, "EXT5V"), (0.9, "CORE")]
    assert [(obs.value, obs.attributes["rail"]) for obs in rail_a_obs] == [(0.9, "EXT5V"), (1.2, "CORE")]
    assert sys_calls["count"] == 1
    assert pmic_calls["count"] == 1
