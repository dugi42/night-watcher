import logging
import os
import threading
from pathlib import Path
from typing import Optional

import cv2

logger = logging.getLogger("night_watcher.recorder")

ASSETS_DIR = Path(os.getenv("ASSETS_DIR", "/assets"))
VIDEO_DIR = ASSETS_DIR / "video"

_FOURCC = cv2.VideoWriter_fourcc(*"mp4v")
_FPS = 20
_FRAME_SIZE = (640, 480)


class VideoRecorder:
    """Writes video frames to /assets/video/{session_id}.mp4 while a session is active."""

    def __init__(self, fps: int = _FPS, frame_size: tuple = _FRAME_SIZE):
        self._fps = fps
        self._frame_size = frame_size
        self._writer: Optional[cv2.VideoWriter] = None
        self._path: Optional[Path] = None
        self._lock = threading.Lock()

    def start(self, session_id: str) -> Path:
        VIDEO_DIR.mkdir(parents=True, exist_ok=True)
        with self._lock:
            if self._writer is not None:
                self._writer.release()
            path = VIDEO_DIR / f"{session_id}.mp4"
            self._writer = cv2.VideoWriter(str(path), _FOURCC, self._fps, self._frame_size)
            self._path = path
            logger.info("Recording started: %s", path)
        return path

    def write_frame(self, frame) -> None:
        with self._lock:
            if self._writer is None:
                return
            h, w = frame.shape[:2]
            if (w, h) != self._frame_size:
                frame = cv2.resize(frame, self._frame_size)
            self._writer.write(frame)

    def stop(self, session_id: str = "") -> Optional[Path]:
        with self._lock:
            if self._writer is None:
                return None
            self._writer.release()
            self._writer = None
            path = self._path
            self._path = None
            logger.info("Recording stopped: %s", path)
        return path

    @property
    def is_recording(self) -> bool:
        with self._lock:
            return self._writer is not None
