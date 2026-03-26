"""Video recorder for detection sessions.

Writes annotated camera frames to ``/assets/video/{session_id}.mp4`` while a
detection session is active. Designed to be wired directly to
:class:`~src.tracker.DetectionTracker` callbacks.

Typical usage
-------------
    recorder = VideoRecorder()
    tracker.set_callbacks(
        on_start=recorder.start,
        on_end=lambda _: recorder.stop(),
    )
"""

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
    """Thread-safe writer of MP4 video files, one file per detection session.

    Parameters
    ----------
    fps:
        Target frame rate for the output video. Defaults to 20.
    frame_size:
        ``(width, height)`` of each frame. Frames that differ are resized.
        Defaults to ``(640, 480)``.
    """

    def __init__(self, fps: int = _FPS, frame_size: tuple[int, int] = _FRAME_SIZE) -> None:
        self._fps = fps
        self._frame_size = frame_size
        self._writer: Optional[cv2.VideoWriter] = None
        self._path: Optional[Path] = None
        self._lock = threading.Lock()

    def start(self, session_id: str) -> Path:
        """Open a new video file for the given session UUID.

        If a recording is already in progress it is stopped first.

        Parameters
        ----------
        session_id:
            UUID of the detection session; used as the filename stem.

        Returns
        -------
        Path
            Absolute path to the newly created video file.
        """
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
        """Write a single frame to the active recording.

        No-ops silently when no recording is in progress.

        Parameters
        ----------
        frame:
            BGR NumPy array as returned by OpenCV. Resized automatically if
            dimensions differ from the configured frame size.
        """
        with self._lock:
            if self._writer is None:
                return
            h, w = frame.shape[:2]
            if (w, h) != self._frame_size:
                frame = cv2.resize(frame, self._frame_size)
            self._writer.write(frame)

    def stop(self) -> Optional[Path]:
        """Finalise and close the active recording.

        Returns
        -------
        Path | None
            Path to the completed video file, or ``None`` if nothing was open.
        """
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
        """``True`` while a video file is open and being written."""
        with self._lock:
            return self._writer is not None
