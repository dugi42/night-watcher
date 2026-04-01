from __future__ import annotations

import importlib
import json
import logging
import sys
from datetime import datetime as real_datetime

import pytest
from fastapi import HTTPException

from src import log_store


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
