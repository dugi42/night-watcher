import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("night_watcher.tracker")

ASSETS_DIR = Path(os.getenv("ASSETS_DIR", "/assets"))
META_DIR = ASSETS_DIR / "meta"
DETECTIONS_FILE = META_DIR / "detections.json"

DISAPPEAR_TIMEOUT = 3.0  # seconds without detection before ending a session


@dataclass
class _ObjectTrack:
    label: str
    first_seen: float
    last_seen: float


@dataclass
class _Session:
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    objects: dict = field(default_factory=dict)  # label -> _ObjectTrack


class DetectionTracker:
    """Tracks detection sessions, persists metadata as JSON, and fires callbacks."""

    def __init__(self, disappear_timeout: float = DISAPPEAR_TIMEOUT):
        self._timeout = disappear_timeout
        self._session: Optional[_Session] = None
        self._on_start = None   # callback(session_id: str)
        self._on_end = None     # callback(session_id: str)

    def set_callbacks(self, on_start=None, on_end=None):
        self._on_start = on_start
        self._on_end = on_end

    def update(self, detections: list[dict]) -> Optional[str]:
        """Feed current-frame detections. Returns active session_id or None."""
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
        """Immediately end the active session (e.g. on stream stop)."""
        if self._session is not None:
            self._end_session(time.time())

    def _end_session(self, end_time: float) -> None:
        session = self._session
        session.end_time = end_time
        self._session = None
        logger.info("Session ended: %s (%.1fs)", session.session_id, end_time - session.start_time)
        self._persist(session)
        if self._on_end:
            self._on_end(session.session_id)

    def _persist(self, session: _Session) -> None:
        META_DIR.mkdir(parents=True, exist_ok=True)
        record = {
            "uuid": session.session_id,
            "start_time": session.start_time,
            "end_time": session.end_time,
            "duration_seconds": round((session.end_time or session.start_time) - session.start_time, 2),
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
            data = []
        data.append(record)
        try:
            DETECTIONS_FILE.write_text(json.dumps(data, indent=2))
        except OSError:
            logger.exception("Failed to write detection metadata")

    @property
    def active_session_id(self) -> Optional[str]:
        return self._session.session_id if self._session else None
