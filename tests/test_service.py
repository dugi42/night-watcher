from __future__ import annotations

import asyncio
import importlib
import json
import logging
import sys
from datetime import datetime as real_datetime

import pytest
from fastapi import HTTPException

from src import log_store
from src import __version__


class _FakeMetric:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def add(self, value, attributes=None) -> None:
        self.calls.append((value, attributes))

    def record(self, value) -> None:
        self.calls.append((value,))


class _FakeOtel:
    def __init__(self) -> None:
        self.frames_processed = _FakeMetric()
        self.frame_processing_ms = _FakeMetric()
        self.detections_total = _FakeMetric()
        self.sessions_started = _FakeMetric()


class _FakeCap:
    def __init__(self, opened: bool = True) -> None:
        self._opened = opened

    def isOpened(self) -> bool:
        return self._opened


class _FakeFrame:
    shape = (24, 32, 3)


class _FakeBuffer:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def tobytes(self) -> bytes:
        return self._payload


@pytest.fixture
def service_module(monkeypatch, tmp_path):
    class _DummySQLiteHandler(logging.Handler):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__()

        def emit(self, record) -> None:
            pass

    monkeypatch.setattr(log_store, "SQLiteLogHandler", _DummySQLiteHandler)
    monkeypatch.setenv("ASSETS_DIR", str(tmp_path))
    sys.modules.pop("src.service", None)
    module = importlib.import_module("src.service")
    module.META_FILE = tmp_path / "meta" / "detections.json"
    module.VIDEO_DIR = tmp_path / "video"
    return module


def test_service_frame_status_and_metrics_endpoints(service_module, monkeypatch) -> None:
    service_module._state = service_module._State()
    service_module._config = service_module._DetectionConfig()
    service_module._stats = service_module._AppStats()

    with pytest.raises(HTTPException) as exc:
        service_module.get_frame()
    assert exc.value.status_code == 503

    monkeypatch.setattr(service_module.time, "time", lambda: 105.0)
    service_module._state.update(b"jpeg-bytes", ["cat"], "session-1", 100.0)
    service_module._stats.start_time = 95.0
    service_module._stats.record_session_start()
    service_module._stats.record_frame(12.5, [{"label": "cat"}, {"label": "cat"}])

    frame_response = service_module.get_frame()
    status = service_module.get_status()
    metrics = service_module.get_app_metrics()

    assert frame_response.body == b"jpeg-bytes"
    assert frame_response.media_type == "image/jpeg"
    assert status == {
        "detecting": True,
        "detection_active": True,
        "session_id": "session-1",
        "detected_classes": ["cat"],
        "frame_captured_at": 100.0,
        "frame_age_ms": 5000,
    }
    assert metrics == {
        "frames_total": 1,
        "sessions_total": 1,
        "avg_processing_ms": 12.5,
        "fps_avg": 0.1,
        "detections_by_class": {"cat": 2},
        "uptime_seconds": 10,
    }
    assert service_module.app.version == __version__


def test_service_detection_config_schedule_and_logs(service_module, monkeypatch) -> None:
    service_module._config = service_module._DetectionConfig()

    class _FakeDatetime:
        @staticmethod
        def now():
            return real_datetime(2026, 4, 1, 23, 0)

        @staticmethod
        def strptime(value: str, fmt: str):
            return real_datetime.strptime(value, fmt)

    monkeypatch.setattr(service_module, "datetime", _FakeDatetime)

    body = service_module._DetectionConfigIn(
        enabled=True,
        schedule_enabled=True,
        schedule_start="20:00",
        schedule_end="06:00",
        conf_threshold=0.55,
    )
    updated = service_module.post_detection_config(body)
    config = service_module.get_detection_config()
    monkeypatch.setattr(service_module, "query_logs", lambda limit, level, since: [{"limit": limit, "level": level, "since": since}])

    assert updated["active"] is True
    assert config["conf_threshold"] == 0.55
    assert config["active"] is True
    assert service_module.get_logs(limit=5, level="INFO", since=10.0) == [
        {"limit": 5, "level": "INFO", "since": 10.0}
    ]


def test_detection_config_handles_disabled_and_same_day_schedule(service_module, monkeypatch) -> None:
    config = service_module._DetectionConfig()

    class _DaytimeDatetime:
        @staticmethod
        def now():
            return real_datetime(2026, 4, 1, 14, 30)

        @staticmethod
        def strptime(value: str, fmt: str):
            return real_datetime.strptime(value, fmt)

    monkeypatch.setattr(service_module, "datetime", _DaytimeDatetime)

    config.update(
        enabled=False,
        schedule_enabled=True,
        schedule_start="08:00",
        schedule_end="18:00",
        conf_threshold=0.42,
    )
    assert config.is_active() is False

    config.update(
        enabled=True,
        schedule_enabled=True,
        schedule_start="08:00",
        schedule_end="18:00",
        conf_threshold=0.42,
    )
    assert config.is_active() is True


def test_service_detection_history_and_video_endpoints(service_module) -> None:
    assert service_module.get_detections() == []

    service_module.META_FILE.parent.mkdir(parents=True, exist_ok=True)
    expected = [{"uuid": "abc"}]
    service_module.META_FILE.write_text(json.dumps(expected))
    assert service_module.get_detections() == expected

    service_module.META_FILE.write_text("{broken")
    with pytest.raises(HTTPException) as exc:
        service_module.get_detections()
    assert exc.value.status_code == 500

    with pytest.raises(HTTPException) as exc:
        service_module.get_video("../missing")
    assert exc.value.status_code == 404

    service_module.VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    video_path = service_module.VIDEO_DIR / "clip.mp4"
    video_path.write_bytes(b"mp4")
    response = service_module.get_video("../clip")

    assert response.path == str(video_path)
    assert response.media_type == "video/mp4"


def test_service_health_stream_and_lifespan(service_module, monkeypatch) -> None:
    service_module._state = service_module._State()
    service_module._state.update(b"stream-frame", [], None, 1.0)
    sleep_calls: list[float] = []

    monkeypatch.setattr(service_module, "get_system_health", lambda: {"cpu": {"percent": 12.0}})
    monkeypatch.setattr(service_module, "get_docker_services", lambda: [{"name": "night-watcher"}])
    monkeypatch.setattr(service_module, "get_pmic_readings", lambda: {"ext5v_v": 5.1})
    monkeypatch.setattr(service_module, "get_power_status", lambda: {"healthy": True})
    monkeypatch.setattr(service_module.time, "sleep", lambda seconds: sleep_calls.append(seconds))

    response = service_module.get_stream()
    chunk = asyncio.run(response.body_iterator.__anext__())
    second_chunk = asyncio.run(response.body_iterator.__anext__())

    assert service_module.health() == {"status": "ok"}
    assert service_module.health_detailed() == {"cpu": {"percent": 12.0}}
    assert service_module.health_docker() == [{"name": "night-watcher"}]
    assert service_module.health_pmic() == {"ext5v_v": 5.1}
    assert service_module.health_power() == {"healthy": True}
    assert chunk == b"--frame\r\nContent-Type: image/jpeg\r\n\r\nstream-frame\r\n"
    assert second_chunk == chunk
    assert response.media_type == "multipart/x-mixed-replace; boundary=frame"
    assert sleep_calls == [0.05]

    calls: list[tuple] = []
    fake_otel = _FakeOtel()

    class _FakeLoop:
        def __init__(self, state, config, stats, otel) -> None:
            calls.append(("init", state, config, stats, otel))

        def start(self) -> None:
            calls.append(("start",))

        def stop(self) -> None:
            calls.append(("stop",))

    monkeypatch.setattr(service_module, "setup_telemetry", lambda: fake_otel)
    monkeypatch.setattr(service_module, "DetectionLoop", _FakeLoop)

    async def _exercise() -> None:
        async with service_module._lifespan(service_module.app):
            assert service_module._otel is fake_otel
            assert isinstance(service_module._loop, _FakeLoop)

    asyncio.run(_exercise())

    assert calls == [
        ("init", service_module._state, service_module._config, service_module._stats, fake_otel),
        ("start",),
        ("stop",),
    ]


def test_detection_loop_start_and_stop_manage_thread(service_module, monkeypatch) -> None:
    created_threads: list[object] = []

    class _FakeThread:
        def __init__(self, target, daemon: bool, name: str) -> None:
            self.target = target
            self.daemon = daemon
            self.name = name
            self.started = False
            self.join_timeout = None
            created_threads.append(self)

        def start(self) -> None:
            self.started = True

        def join(self, timeout: float) -> None:
            self.join_timeout = timeout

    monkeypatch.setattr(service_module.threading, "Thread", _FakeThread)

    loop = service_module.DetectionLoop(
        service_module._State(),
        service_module._DetectionConfig(),
        service_module._AppStats(),
        _FakeOtel(),
    )
    loop.start()
    loop.stop()

    assert loop._running is False
    assert len(created_threads) == 1
    assert created_threads[0].target == loop._run
    assert created_threads[0].daemon is True
    assert created_threads[0].name == "detection-loop"
    assert created_threads[0].started is True
    assert created_threads[0].join_timeout == 5


def test_detection_loop_run_processes_active_and_inactive_frames(service_module, monkeypatch) -> None:
    service_module._state = service_module._State()
    stats = service_module._AppStats()
    otel = _FakeOtel()
    detector_box: dict[str, object] = {}
    async_detector_box: dict[str, object] = {}
    tracker_box: dict[str, object] = {}
    recorder_box: dict[str, object] = {}
    released_caps: list[object] = []
    config_calls = {"count": 0}
    frame_reads = [
        (True, _FakeFrame()),
        (True, _FakeFrame()),
    ]

    class _FakeConfig:
        def snapshot(self) -> dict[str, float]:
            return {"conf_threshold": 0.55}

        def is_active(self) -> bool:
            config_calls["count"] += 1
            return config_calls["count"] == 1

    class _FakeYoloDetector:
        def __init__(self, model_name: str, conf_threshold: float) -> None:
            self.model_name = model_name
            self.conf_threshold = conf_threshold
            detector_box["instance"] = self

    class _FakeAsyncDetector:
        def __init__(self, detector) -> None:
            self.detector = detector
            self.last_inference_ms = 14.2
            self.closed = False
            async_detector_box["instance"] = self

        def process_frame(self, frame):
            return _FakeFrame(), None, [{"label": "cat"}, {"label": "dog"}]

        def close(self) -> None:
            self.closed = True

    class _FakeRecorder:
        def __init__(self) -> None:
            self.is_recording = False
            self.started: list[str] = []
            self.stopped = 0
            self.written = 0
            recorder_box["instance"] = self

        def start(self, session_id: str) -> None:
            self.is_recording = True
            self.started.append(session_id)

        def stop(self) -> None:
            self.is_recording = False
            self.stopped += 1

        def write_frame(self, frame) -> None:
            self.written += 1

    class _FakeTracker:
        def __init__(self) -> None:
            self.force_end_calls = 0
            self.on_start = None
            self.on_end = None
            tracker_box["instance"] = self

        def set_callbacks(self, on_start, on_end) -> None:
            self.on_start = on_start
            self.on_end = on_end

        def update(self, detections):
            self.on_start("session-1")
            return "session-1"

        def force_end(self) -> None:
            self.force_end_calls += 1
            if self.on_end is not None:
                self.on_end("session-1")

    monkeypatch.setenv("YOLO_MODEL_PATH", "/tmp/model.pt")
    monkeypatch.setattr(service_module, "YoloDetector", _FakeYoloDetector)
    monkeypatch.setattr(service_module, "AsyncYoloDetector", _FakeAsyncDetector)
    monkeypatch.setattr(service_module, "VideoRecorder", _FakeRecorder)
    monkeypatch.setattr(service_module, "DetectionTracker", _FakeTracker)
    monkeypatch.setattr(service_module, "open_camera", lambda index: _FakeCap())
    monkeypatch.setattr(service_module, "configure_camera", lambda cap, width, height, fps: detector_box.setdefault("camera", (cap, width, height, fps)))
    monkeypatch.setattr(service_module, "read_frame", lambda cap: frame_reads.pop(0))
    monkeypatch.setattr(service_module, "release_camera", lambda cap: released_caps.append(cap))
    monkeypatch.setattr(service_module.cv2, "putText", lambda *args, **kwargs: None)
    monkeypatch.setattr(service_module.cv2, "imencode", lambda ext, frame, quality: (True, _FakeBuffer(b"encoded-frame")))

    loop = service_module.DetectionLoop(service_module._state, _FakeConfig(), stats, otel)
    loop._running = True

    def fake_sleep(seconds: float) -> None:
        if seconds == 0.05 and not frame_reads:
            loop._running = False

    monkeypatch.setattr(service_module.time, "sleep", fake_sleep)
    monkeypatch.setattr(service_module.time, "time", lambda: 123.0)

    loop._run()

    frame, classes, session_id, captured_at = service_module._state.snapshot()
    assert frame == b"encoded-frame"
    assert classes == []
    assert session_id is None
    assert captured_at == 123.0
    assert stats.frames_total == 2
    assert stats.sessions_total == 1
    assert stats.detections_by_class == {"cat": 1, "dog": 1}
    assert otel.sessions_started.calls == [(1, None)]
    assert otel.frames_processed.calls == [(1, None), (1, None)]
    assert otel.frame_processing_ms.calls == [(14.2,), (14.2,)]
    assert otel.detections_total.calls == [(1, {"class": "cat"}), (1, {"class": "dog"})]
    assert detector_box["camera"][1:] == (640, 480, 20)
    assert detector_box["instance"].model_name == "/tmp/model.pt"
    assert detector_box["instance"].conf_threshold == 0.55
    assert recorder_box["instance"].started == ["session-1"]
    assert recorder_box["instance"].written == 1
    assert recorder_box["instance"].stopped == 4
    assert tracker_box["instance"].force_end_calls == 2
    assert async_detector_box["instance"].closed is True
    assert len(released_caps) == 1


def test_detection_loop_run_closes_async_detector_when_camera_fails(service_module, monkeypatch) -> None:
    async_detector_box: dict[str, object] = {}

    class _FakeYoloDetector:
        def __init__(self, model_name: str, conf_threshold: float) -> None:
            self.model_name = model_name
            self.conf_threshold = conf_threshold

    class _FakeAsyncDetector:
        def __init__(self, detector) -> None:
            self.closed = False
            async_detector_box["instance"] = self

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(service_module, "YoloDetector", _FakeYoloDetector)
    monkeypatch.setattr(service_module, "AsyncYoloDetector", _FakeAsyncDetector)
    monkeypatch.setattr(service_module, "VideoRecorder", lambda: None)
    monkeypatch.setattr(service_module, "DetectionTracker", lambda: type("Tracker", (), {"set_callbacks": lambda *args, **kwargs: None})())
    monkeypatch.setattr(service_module, "open_camera", lambda index: _FakeCap(opened=False))

    loop = service_module.DetectionLoop(
        service_module._State(),
        service_module._DetectionConfig(),
        service_module._AppStats(),
        _FakeOtel(),
    )
    loop._run()

    assert async_detector_box["instance"].closed is True


def test_detection_loop_run_retries_on_failed_frame_read(service_module, monkeypatch) -> None:
    async_detector_box: dict[str, object] = {}
    released_caps: list[object] = []
    sleep_calls: list[float] = []

    class _FakeYoloDetector:
        def __init__(self, model_name: str, conf_threshold: float) -> None:
            self.conf_threshold = conf_threshold

    class _FakeAsyncDetector:
        def __init__(self, detector) -> None:
            self.closed = False
            self.last_inference_ms = 0.0
            async_detector_box["instance"] = self

        def close(self) -> None:
            self.closed = True

    class _FakeTracker:
        def set_callbacks(self, on_start, on_end) -> None:
            self.on_start = on_start
            self.on_end = on_end

        def force_end(self) -> None:
            pass

    monkeypatch.setattr(service_module, "YoloDetector", _FakeYoloDetector)
    monkeypatch.setattr(service_module, "AsyncYoloDetector", _FakeAsyncDetector)
    monkeypatch.setattr(service_module, "VideoRecorder", lambda: type("Recorder", (), {"stop": lambda self: None})())
    monkeypatch.setattr(service_module, "DetectionTracker", _FakeTracker)
    monkeypatch.setattr(service_module, "open_camera", lambda index: _FakeCap())
    monkeypatch.setattr(service_module, "configure_camera", lambda cap, width, height, fps: None)
    monkeypatch.setattr(service_module, "read_frame", lambda cap: (False, None))
    monkeypatch.setattr(service_module, "release_camera", lambda cap: released_caps.append(cap))

    loop = service_module.DetectionLoop(
        service_module._State(),
        service_module._DetectionConfig(),
        service_module._AppStats(),
        _FakeOtel(),
    )
    loop._running = True

    def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        if seconds == 0.1:
            loop._running = False

    monkeypatch.setattr(service_module.time, "sleep", fake_sleep)

    loop._run()

    assert sleep_calls == [0.1]
    assert async_detector_box["instance"].closed is True
    assert len(released_caps) == 1
