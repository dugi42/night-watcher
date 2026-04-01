from __future__ import annotations

from src import camera


class _FakeCapture:
    def __init__(self, source: str | int, opened: bool = True) -> None:
        self.source = source
        self._opened = opened
        self.released = False
        self.set_calls: list[tuple[int, int]] = []

    def isOpened(self) -> bool:
        return self._opened

    def release(self) -> None:
        self.released = True
        self._opened = False

    def set(self, prop: int, value: int) -> None:
        self.set_calls.append((prop, value))

    def read(self) -> tuple[bool, object]:
        return True, {"source": self.source}


def test_camera_sources_deduplicate_preferred_path(monkeypatch) -> None:
    monkeypatch.setenv("CAMERA_DEVICE", "/dev/video2")

    assert camera._camera_sources(2) == ["/dev/video2", 2]


def test_open_camera_reuses_shared_handle(monkeypatch) -> None:
    shared = _FakeCapture("/dev/video0")
    monkeypatch.setattr(camera, "_SHARED_CAP", shared)

    assert camera.open_camera() is shared


def test_open_camera_retries_sources_and_releases_failed_handles(monkeypatch) -> None:
    created: list[_FakeCapture] = []

    def fake_video_capture(source: str | int, backend: int) -> _FakeCapture:
        opened = source == "/dev/video0"
        cap = _FakeCapture(source, opened=opened)
        created.append(cap)
        return cap

    monkeypatch.setattr(camera.cv2, "VideoCapture", fake_video_capture)
    monkeypatch.setattr(camera.time, "sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(camera, "_SHARED_CAP", None)
    monkeypatch.delenv("CAMERA_DEVICE", raising=False)

    cap = camera.open_camera(index=0, retries=1)

    assert cap is not None
    assert cap.source == "/dev/video0"
    assert [c.source for c in created] == ["/dev/video0"]
    assert created[0].released is False

    camera.release_camera(cap)
    assert camera._SHARED_CAP is None


def test_open_camera_returns_none_after_failures(monkeypatch) -> None:
    monkeypatch.setattr(camera.cv2, "VideoCapture", lambda *_args, **_kwargs: _FakeCapture(0, opened=False))
    monkeypatch.setattr(camera, "list_video_devices", lambda: ["/dev/video0"])
    monkeypatch.setattr(camera.time, "sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(camera, "_SHARED_CAP", None)

    assert camera.open_camera(index=0, retries=2) is None


def test_configure_camera_and_read_frame() -> None:
    cap = _FakeCapture("/dev/video0")

    camera.configure_camera(cap, width=800, height=600, fps=15)

    assert cap.set_calls == [
        (camera.cv2.CAP_PROP_FRAME_WIDTH, 800),
        (camera.cv2.CAP_PROP_FRAME_HEIGHT, 600),
        (camera.cv2.CAP_PROP_FPS, 15),
        (camera.cv2.CAP_PROP_BUFFERSIZE, 1),
    ]
    assert camera.read_frame(cap) == (True, {"source": "/dev/video0"})
