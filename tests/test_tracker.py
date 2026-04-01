from __future__ import annotations

import json

from src import tracker


def test_detection_tracker_persists_completed_session(tmp_path, monkeypatch) -> None:
    detections_file = tmp_path / "meta" / "detections.json"
    monkeypatch.setattr(tracker, "META_DIR", detections_file.parent)
    monkeypatch.setattr(tracker, "DETECTIONS_FILE", detections_file)

    times = iter([100.0, 101.0, 105.5])
    monkeypatch.setattr(tracker.time, "time", lambda: next(times))

    started: list[str] = []
    ended: list[str] = []

    session_tracker = tracker.DetectionTracker(disappear_timeout=3.0)
    session_tracker.set_callbacks(on_start=started.append, on_end=ended.append)

    session_id = session_tracker.update([{"label": "cat"}, {"label": "cat"}])
    assert session_id is not None
    session_tracker._session.start_time = 100.0  # type: ignore[union-attr]

    assert session_tracker.update([{"label": "dog"}]) == session_id
    assert session_tracker.update([]) is None

    saved = json.loads(detections_file.read_text())
    assert len(saved) == 1
    assert saved[0]["uuid"] == session_id
    assert saved[0]["duration_seconds"] == 5.5
    assert {obj["label"] for obj in saved[0]["objects"]} == {"cat", "dog"}
    assert started == [session_id]
    assert ended == [session_id]
    assert session_tracker.active_session_id is None
