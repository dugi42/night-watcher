"""Detection session tracker.

Groups individual YOLO detections into continuous *sessions* — a period during
which at least one target object remains visible. Each session gets a UUID and
is persisted to ``/assets/meta/detections.json`` when it ends.

Typical usage
-------------
    tracker = DetectionTracker()
    recorder = VideoRecorder()
    tracker.set_callbacks(on_start=recorder.start, on_end=lambda _: recorder.stop())

    for detections in yolo_results:
        session_id = tracker.update(detections)
"""

import json
import logging
import os
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("night_watcher.tracker")

ASSETS_DIR = Path(os.getenv("ASSETS_DIR", "/assets"))
META_DIR = ASSETS_DIR / "meta"
DETECTIONS_FILE = META_DIR / "detections.json"

#: Seconds without a detection before declaring an object gone.
DISAPPEAR_TIMEOUT = 3.0


@dataclass
class _ObjectTrack:
    """Tracks one class of object within a session."""

    label: str
    first_seen: float
    last_seen: float


@dataclass
class _Session:
    """A continuous period where at least one target object is visible."""

    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    #: Maps class label to its track within this session.
    objects: dict[str, _ObjectTrack] = field(default_factory=dict)


class DetectionTracker:
    """Tracks detection sessions, persists metadata as JSON, and fires callbacks.

    A session begins when the first detection arrives and ends when all tracked
    classes have been absent for longer than *disappear_timeout* seconds.

    Parameters
    ----------
    disappear_timeout:
        Seconds of inactivity before a session is closed. Defaults to 3.0.
    """

    def __init__(self, disappear_timeout: float = DISAPPEAR_TIMEOUT) -> None:
        self._timeout = disappear_timeout
        self._session: Optional[_Session] = None
        self._on_start: Callable[[str], Any] | None = None
        self._on_end: Callable[[str], Any] | None = None

    def set_callbacks(self, on_start: Callable[[str], Any] | None = None, on_end: Callable[[str], Any] | None = None) -> None:
        """Register lifecycle callbacks.

        Parameters
        ----------
        on_start:
            Called with the new session UUID when a session begins.
        on_end:
            Called with the session UUID when a session ends.
        """
        self._on_start = on_start
        self._on_end = on_end

    def update(self, detections: list[dict[str, Any]]) -> Optional[str]:
        """Process the current frame's detections and return the active session ID.

        Parameters
        ----------
        detections:
            List of detection dicts with at least a ``"label"`` key, as
            returned by :class:`~src.detector.YoloDetector`.

        Returns
        -------
        str | None
            The active session UUID, or ``None`` if no session is open.
        """
        now = time.time()
        labels = {d["label"] for d in detections}

        if labels:
            if self._session is None:
                self._session = _Session()
                logger.info("Session started: %s", self._session.session_id)
                if self._on_start:
                    self._on_start(self._session.session_id)

            for label in labels:
                if label in self._session.objects:
                    self._session.objects[label].last_seen = now
                else:
                    self._session.objects[label] = _ObjectTrack(
                        label=label, first_seen=now, last_seen=now
                    )

        if self._session is not None and self._session.objects:
            all_gone = all(
                now - t.last_seen > self._timeout
                for t in self._session.objects.values()
            )
            if all_gone:
                self._end_session(now)

        return self._session.session_id if self._session else None

    def force_end(self) -> None:
        """Immediately close any open session, e.g. when the stream stops."""
        if self._session is not None:
            logger.info("Forcing session end: %s", self._session.session_id)
            self._end_session(time.time())

    @property
    def active_session_id(self) -> Optional[str]:
        """The UUID of the currently open session, or ``None``."""
        return self._session.session_id if self._session else None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _end_session(self, end_time: float) -> None:
        """Finalise and persist the current session, then fire the callback."""
        session = self._session
        session.end_time = end_time
        self._session = None
        duration = end_time - session.start_time
        logger.info("Session ended: %s (%.1fs)", session.session_id, duration)
        self._persist(session)
        if self._on_end:
            self._on_end(session.session_id)

    def _persist(self, session: _Session) -> None:
        """Append the finished session to the JSON history file."""
        META_DIR.mkdir(parents=True, exist_ok=True)
        record = {
            "uuid": session.session_id,
            "start_time": session.start_time,
            "end_time": session.end_time,
            "duration_seconds": round(
                (session.end_time or session.start_time) - session.start_time, 2
            ),
            "objects": [
                {
                    "label": t.label,
                    "first_seen": t.first_seen,
                    "last_seen": t.last_seen,
                    "duration_seconds": round(t.last_seen - t.first_seen, 2),
                }
                for t in session.objects.values()
            ],
        }
        try:
            data = json.loads(DETECTIONS_FILE.read_text()) if DETECTIONS_FILE.exists() else []
        except (json.JSONDecodeError, OSError):
            logger.warning("Could not read existing detections; starting fresh")
            data = []
        data.append(record)
        try:
            DETECTIONS_FILE.write_text(json.dumps(data, indent=2))
            logger.debug("Persisted session %s to %s", session.session_id, DETECTIONS_FILE)
        except OSError:
            logger.exception("Failed to write detection metadata")
