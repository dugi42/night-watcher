"""FastAPI detection service — runs on the Raspberry Pi inside Docker.

Opens the camera on startup, runs YOLO object detection continuously in a
background thread, feeds the tracker and recorder, and exposes HTTP endpoints
so the Streamlit client can consume live video and detection history.

Endpoints
---------
GET /health                 Liveness probe.
GET /frame                  Latest annotated JPEG frame (for polling clients).
GET /stream                 MJPEG video stream (for browser <img> tags).
GET /status                 Current detection state as JSON.
GET /detections             Full history of recorded sessions from detections.json.
GET /video/{uuid}           Serve a recorded MP4 by session UUID.
GET /detection/config       Current enable/schedule configuration.
POST /detection/config      Update enable/schedule configuration.
"""

import json
import logging
import os
import threading
import time
from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import cv2
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel

from src.camera import configure_camera, open_camera, read_frame, release_camera
from src.detector import AsyncYoloDetector, YoloDetector
from src.recorder import VideoRecorder
from src.tracker import DetectionTracker
from src.utils import setup_logging

setup_logging()
logger = logging.getLogger("night_watcher.service")

ASSETS_DIR = Path(os.getenv("ASSETS_DIR", "/assets"))
META_FILE = ASSETS_DIR / "meta" / "detections.json"
VIDEO_DIR = ASSETS_DIR / "video"

_JPEG_QUALITY = [cv2.IMWRITE_JPEG_QUALITY, 80]


class _State:
    """Thread-safe holder for the latest frame and detection metadata."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.frame: Optional[bytes] = None
        self.detected_classes: list[str] = []
        self.session_id: Optional[str] = None

    def update(self, frame: bytes, classes: list[str], session_id: Optional[str]) -> None:
        """Atomically replace the latest frame and detection state."""
        with self._lock:
            self.frame = frame
            self.detected_classes = classes
            self.session_id = session_id

    def snapshot(self) -> tuple[Optional[bytes], list[str], Optional[str]]:
        """Return a consistent snapshot of (frame, classes, session_id)."""
        with self._lock:
            return self.frame, list(self.detected_classes), self.session_id


class _DetectionConfig:
    """Thread-safe holder for detection enable/schedule configuration."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.enabled: bool = True
        self.schedule_enabled: bool = False
        self.schedule_start: str = "20:00"
        self.schedule_end: str = "06:00"
        self.conf_threshold: float = 0.35

    def is_active(self) -> bool:
        """Return True if detection should run right now."""
        with self._lock:
            if not self.enabled:
                return False
            if not self.schedule_enabled:
                return True
            now = datetime.now().time()
            start_t = datetime.strptime(self.schedule_start, "%H:%M").time()
            end_t = datetime.strptime(self.schedule_end, "%H:%M").time()
            if start_t <= end_t:
                return start_t <= now <= end_t
            # Overnight schedule (e.g. 22:00 → 06:00)
            return now >= start_t or now <= end_t

    def snapshot(self) -> dict[str, Any]:
        """Return a copy of the current configuration as a plain dict.

        Returns
        -------
        dict[str, Any]
            Keys: ``enabled``, ``schedule_enabled``, ``schedule_start``,
            ``schedule_end``, ``conf_threshold``.  Does not include
            ``active`` — call :meth:`is_active` separately so the lock
            is not held twice.
        """
        with self._lock:
            return {
                "enabled": self.enabled,
                "schedule_enabled": self.schedule_enabled,
                "schedule_start": self.schedule_start,
                "schedule_end": self.schedule_end,
                "conf_threshold": self.conf_threshold,
            }

    def update(
        self,
        enabled: bool,
        schedule_enabled: bool,
        schedule_start: str,
        schedule_end: str,
        conf_threshold: float,
    ) -> None:
        """Atomically replace the full configuration.

        Parameters
        ----------
        enabled:
            When ``False``, detection is unconditionally paused regardless
            of the schedule.
        schedule_enabled:
            When ``True``, detection only runs between *schedule_start* and
            *schedule_end*.
        schedule_start:
            Wall-clock time in ``HH:MM`` format at which detection begins.
        schedule_end:
            Wall-clock time in ``HH:MM`` format at which detection ends.
            If earlier than *schedule_start* the schedule is treated as
            overnight (e.g. ``"22:00"`` → ``"06:00"``).
        conf_threshold:
            Minimum YOLO confidence score (0–1) for a detection to be
            reported. Applied live without restarting the detection loop.
        """
        with self._lock:
            self.enabled = enabled
            self.schedule_enabled = schedule_enabled
            self.schedule_start = schedule_start
            self.schedule_end = schedule_end
            self.conf_threshold = conf_threshold


class DetectionLoop:
    """Runs the camera capture and YOLO inference loop in a daemon thread.

    On start, opens the camera, loads the YOLO model, and continuously reads
    frames. Each frame is passed to the async detector; results feed the
    tracker (session lifecycle) and recorder (video writing). The latest
    annotated JPEG is stored in shared state for the API to serve.
    """

    def __init__(self, state: _State, config: "_DetectionConfig") -> None:
        self._state = state
        self._config = config
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start the detection loop in a background daemon thread."""
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="detection-loop")
        self._thread.start()
        logger.info("Detection loop started")

    def stop(self) -> None:
        """Signal the detection loop to stop and wait for it to finish."""
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=5)
        logger.info("Detection loop stopped")

    def _run(self) -> None:
        """Main loop: open camera, run inference, update shared state."""
        model_path = os.getenv("YOLO_MODEL_PATH", "/app/models/yolo11n.pt")
        logger.info("Loading YOLO model from %s", model_path)
        detector = YoloDetector(model_name=model_path, conf_threshold=0.35)
        async_detector = AsyncYoloDetector(detector)

        recorder = VideoRecorder()
        tracker = DetectionTracker()
        tracker.set_callbacks(
            on_start=recorder.start,
            on_end=lambda _: recorder.stop(),
        )

        cap = open_camera(0)
        if cap is None or not cap.isOpened():
            logger.error("Failed to open camera — detection loop will not run")
            return

        configure_camera(cap, width=640, height=480, fps=20)
        logger.info("Camera opened; entering capture loop")

        was_active = True
        last_conf = detector.conf_threshold
        try:
            while self._running:
                ret, frame = read_frame(cap)
                if not ret:
                    logger.warning("Failed to read frame; retrying")
                    time.sleep(0.1)
                    continue

                # Sync confidence threshold from config without restarting
                current_conf = self._config.snapshot()["conf_threshold"]
                if current_conf != last_conf:
                    detector.conf_threshold = current_conf
                    last_conf = current_conf
                    logger.info("Confidence threshold updated to %.2f", current_conf)

                detection_active = self._config.is_active()

                if detection_active:
                    annotated, _, detections = async_detector.process_frame(frame)
                    session_id = tracker.update(detections)
                    if recorder.is_recording:
                        # Write annotated frame so stored video includes
                        # bounding boxes, labels, and confidence scores
                        recorder.write_frame(annotated)
                else:
                    if was_active:
                        # Transition: active → inactive — end any open session
                        tracker.force_end()
                        recorder.stop()
                        logger.info("Detection paused (disabled or outside schedule)")
                    annotated = frame
                    detections = []
                    session_id = None

                was_active = detection_active

                ok, buf = cv2.imencode(".jpg", annotated, _JPEG_QUALITY)
                if ok:
                    classes = sorted({d["label"] for d in detections})
                    self._state.update(buf.tobytes(), classes, session_id)

                time.sleep(0.05)  # ~20 FPS target
        finally:
            tracker.force_end()
            recorder.stop()
            release_camera(cap)
            async_detector.close()
            logger.info("Camera released")


_state = _State()
_config = _DetectionConfig()
_loop = DetectionLoop(_state, _config)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Start the detection loop on startup; stop it on shutdown."""
    _loop.start()
    yield
    _loop.stop()


app = FastAPI(title="Night Watcher", version="1.0.0", lifespan=_lifespan)


@app.get("/health", summary="Liveness probe")
def health() -> dict[str, str]:
    """Return service status."""
    return {"status": "ok"}


@app.get("/frame", summary="Latest annotated JPEG frame")
def get_frame() -> Response:
    """Return the most recent annotated camera frame as a JPEG image."""
    frame, _, _ = _state.snapshot()
    if frame is None:
        raise HTTPException(status_code=503, detail="No frame available yet")
    return Response(content=frame, media_type="image/jpeg")


@app.get("/stream", summary="MJPEG video stream")
def get_stream() -> StreamingResponse:
    """Stream annotated frames as multipart MJPEG.

    Browsers can consume this directly via an <img> tag without CORS issues.
    """

    def _generate() -> Generator[bytes, None, None]:
        while True:
            frame, _, _ = _state.snapshot()
            if frame:
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
            time.sleep(0.05)

    return StreamingResponse(
        _generate(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@app.get("/status", summary="Current detection state")
def get_status() -> dict[str, Any]:
    """Return the active session ID and currently detected classes."""
    _, classes, session_id = _state.snapshot()
    return {
        "detecting": len(classes) > 0,
        "detection_active": _config.is_active(),
        "session_id": session_id,
        "detected_classes": classes,
    }


class _DetectionConfigIn(BaseModel):
    """Request body for ``POST /detection/config``.

    Attributes
    ----------
    enabled:
        Master switch for the detection system.
    schedule_enabled:
        Whether to restrict detection to the time window below.
    schedule_start:
        Start of the active window in ``HH:MM`` (24-hour) format.
    schedule_end:
        End of the active window in ``HH:MM`` (24-hour) format.
        May be earlier than *schedule_start* for overnight windows.
    """

    enabled: bool
    schedule_enabled: bool
    schedule_start: str = "20:00"
    schedule_end: str = "06:00"
    conf_threshold: float = 0.35


@app.get("/detection/config", summary="Get detection configuration")
def get_detection_config() -> dict[str, Any]:
    """Return the current detection enable/schedule configuration."""
    cfg = _config.snapshot()
    cfg["active"] = _config.is_active()
    return cfg


@app.post("/detection/config", summary="Update detection configuration")
def post_detection_config(body: _DetectionConfigIn) -> dict[str, Any]:
    """Update the detection enable/schedule configuration."""
    _config.update(
        body.enabled,
        body.schedule_enabled,
        body.schedule_start,
        body.schedule_end,
        body.conf_threshold,
    )
    logger.info(
        "Detection config updated: enabled=%s schedule=%s %s-%s conf=%.2f",
        body.enabled, body.schedule_enabled, body.schedule_start, body.schedule_end,
        body.conf_threshold,
    )
    cfg = _config.snapshot()
    cfg["active"] = _config.is_active()
    return cfg


@app.get("/detections", summary="All recorded detection sessions")
def get_detections() -> list[dict[str, Any]]:
    """Return the full history of detection sessions from detections.json."""
    if not META_FILE.exists():
        return []
    try:
        return json.loads(META_FILE.read_text())
    except Exception:
        logger.exception("Failed to read detections file")
        raise HTTPException(status_code=500, detail="Could not read detection history")


@app.get("/video/{uuid}", summary="Serve a recorded session video")
def get_video(uuid: str) -> FileResponse:
    """Stream the MP4 recording for the given session UUID."""
    # Sanitise the uuid to prevent path traversal
    safe_name = Path(uuid).name
    path = VIDEO_DIR / f"{safe_name}.mp4"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Video not found")
    return FileResponse(str(path), media_type="video/mp4")
