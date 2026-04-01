from __future__ import annotations

import numpy as np

from src import recorder


class _FakeWriter:
    def __init__(self, path: str, fourcc: int, fps: int, frame_size: tuple[int, int]) -> None:
        self.path = path
        self.fourcc = fourcc
        self.fps = fps
        self.frame_size = frame_size
        self.frames: list[object] = []
        self.released = False

    def write(self, frame) -> None:
        self.frames.append(frame)

    def release(self) -> None:
        self.released = True


def test_video_recorder_start_write_resize_and_stop(monkeypatch, tmp_path) -> None:
    created: list[_FakeWriter] = []

    def fake_writer(path: str, fourcc: int, fps: int, frame_size: tuple[int, int]) -> _FakeWriter:
        writer = _FakeWriter(path, fourcc, fps, frame_size)
        created.append(writer)
        return writer

    resize_calls: list[tuple[tuple[int, int], tuple[int, int]]] = []

    def fake_resize(frame, size: tuple[int, int]):
        resize_calls.append((frame.shape[:2], size))
        return {"resized_to": size}

    monkeypatch.setattr(recorder, "VIDEO_DIR", tmp_path / "video")
    monkeypatch.setattr(recorder.cv2, "VideoWriter", fake_writer)
    monkeypatch.setattr(recorder.cv2, "resize", fake_resize)

    rec = recorder.VideoRecorder(fps=15, frame_size=(320, 240))
    path = rec.start("session-1")

    assert path == tmp_path / "video" / "session-1.mp4"
    assert rec.is_recording is True

    rec.write_frame(np.zeros((480, 640, 3), dtype=np.uint8))
    rec.write_frame(np.zeros((240, 320, 3), dtype=np.uint8))
    stopped_path = rec.stop()

    assert resize_calls == [((480, 640), (320, 240))]
    assert created[0].frames[0] == {"resized_to": (320, 240)}
    assert created[0].frames[1].shape == (240, 320, 3)
    assert created[0].released is True
    assert stopped_path == path
    assert rec.is_recording is False


def test_video_recorder_replaces_existing_writer_and_handles_noop_stop(monkeypatch, tmp_path) -> None:
    created: list[_FakeWriter] = []
    monkeypatch.setattr(recorder, "VIDEO_DIR", tmp_path / "video")
    monkeypatch.setattr(
        recorder.cv2,
        "VideoWriter",
        lambda path, fourcc, fps, frame_size: created.append(_FakeWriter(path, fourcc, fps, frame_size)) or created[-1],
    )

    rec = recorder.VideoRecorder()
    first = rec.start("first")
    second = rec.start("second")

    assert created[0].released is True
    assert first.name == "first.mp4"
    assert second.name == "second.mp4"
    assert rec.stop() == second
    assert rec.stop() is None
