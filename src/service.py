"""FastAPI detection service — runs on the Raspberry Pi inside Docker.

Opens the camera on startup, runs YOLO object detection continuously in a
background thread, feeds the tracker and recorder, and exposes HTTP endpoints
so the Streamlit client can consume live video and detection history.

Endpoints
---------
GET /health                 Liveness probe.
GET /health/detailed        System health (CPU, memory, disk, temperature).
GET /health/docker          Running Docker container list.
GET /frame                  Latest annotated JPEG frame (for polling clients).
GET /stream                 MJPEG video stream (for browser <img> tags).
GET /status                 Current detection state as JSON.
GET /metrics/app            Application runtime metrics snapshot (JSON).
GET /logs                   Recent structured log entries from SQLite.
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
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel

from src.camera import configure_camera, open_camera, read_frame, release_camera
from src.detector import AsyncYoloDetector, YoloDetector
from src.health import get_docker_services, get_pmic_readings, get_power_status, get_system_health
from src.log_store import SQLiteLogHandler, query_logs
from src.recorder import VideoRecorder
from src.telemetry import AppMetrics, setup_health_telemetry, setup_telemetry
from src.tracker import DetectionTracker
from src.utils import setup_logging
from src import __version__

setup_logging()
logger = logging.getLogger("night_watcher.service")

# Attach SQLite log handler so all application logs are queryable via /logs
_sqlite_handler = SQLiteLogHandler()
_sqlite_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s — %(message)s"))
logging.getLogger("night_watcher").addHandler(_sqlite_handler)

ASSETS_DIR = Path(os.getenv("ASSETS_DIR", "/assets"))
META_FILE = ASSETS_DIR / "meta" / "detections.json"
VIDEO_DIR = ASSETS_DIR / "video"

_JPEG_QUALITY = [cv2.IMWRITE_JPEG_QUALITY, 80]
_TS_FONT = cv2.FONT_HERSHEY_SIMPLEX
_TS_COLOR = (0, 255, 0)  # green


class _State:
    """Thread-safe holder for the latest frame and detection metadata."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.frame: Optional[bytes] = None
        self.detected_classes: list[str] = []
        self.session_id: Optional[str] = None
        self.frame_captured_at: float = 0.0

    def update(
        self,
        frame: bytes,
        classes: list[str],
        session_id: Optional[str],
        captured_at: float,
    ) -> None:
        """Atomically replace the latest frame and detection state."""
        with self._lock:
            self.frame = frame
            self.detected_classes = classes
            self.session_id = session_id
            self.frame_captured_at = captured_at

    def snapshot(self) -> tuple[Optional[bytes], list[str], Optional[str], float]:
        """Return a consistent snapshot of (frame, classes, session_id, captured_at)."""
        with self._lock:
            return self.frame, list(self.detected_classes), self.session_id, self.frame_captured_at


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


class _AppStats:
    """Thread-safe accumulator for application runtime metrics."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.frames_total: int = 0
        self.sessions_total: int = 0
        self.processing_ms_sum: float = 0.0
        self.detections_by_class: dict[str, int] = {}
        self.start_time: float = time.time()

    def record_frame(self, processing_ms: float, detections: list[dict[str, Any]]) -> None:
        """Record a processed frame and its detections."""
        with self._lock:
            self.frames_total += 1
            self.processing_ms_sum += processing_ms
            for d in detections:
                cls = d["label"]
                self.detections_by_class[cls] = self.detections_by_class.get(cls, 0) + 1

    def record_session_start(self) -> None:
        """Increment the session counter."""
        with self._lock:
            self.sessions_total += 1

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-serialisable snapshot of all runtime stats."""
        with self._lock:
            uptime = time.time() - self.start_time
            avg_ms = self.processing_ms_sum / self.frames_total if self.frames_total else 0.0
            fps = self.frames_total / uptime if uptime > 0 else 0.0
            return {
                "frames_total": self.frames_total,
                "sessions_total": self.sessions_total,
                "avg_processing_ms": round(avg_ms, 2),
                "fps_avg": round(fps, 2),
                "detections_by_class": dict(self.detections_by_class),
                "uptime_seconds": round(uptime),
            }


class DetectionLoop:
    """Runs the camera capture and YOLO inference loop in a daemon thread.

    On start, opens the camera, loads the YOLO model, and continuously reads
    frames. Each frame is passed to the async detector; results feed the
    tracker (session lifecycle) and recorder (video writing). The latest
    annotated JPEG is stored in shared state for the API to serve.
    """

    def __init__(
        self,
        state: _State,
        config: "_DetectionConfig",
        stats: _AppStats,
        otel: AppMetrics,
    ) -> None:
        self._state = state
        self._config = config
        self._stats = stats
        self._otel = otel
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

        def _on_session_start(session_id: str) -> None:
            recorder.start(session_id)
            self._stats.record_session_start()
            self._otel.sessions_started.add(1)

        tracker.set_callbacks(
            on_start=_on_session_start,
            on_end=lambda _: recorder.stop(),
        )

        cap = open_camera(0)
        if cap is None or not cap.isOpened():
            async_detector.close()
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

                # Use the actual YOLO inference duration measured inside the
                # background worker thread (set by AsyncYoloDetector after
                # each completed inference). This excludes camera I/O and
                # is 0.0 until the first inference result arrives.
                processing_ms = async_detector.last_inference_ms

                # Overlay live timestamp on every frame so the stream shows
                # real-time clock — confirms the feed is actually live
                ts_text = datetime.now().strftime("%H:%M:%S")
                h, w = annotated.shape[:2]
                cv2.putText(
                    annotated, ts_text, (8, h - 10),
                    _TS_FONT, 0.65, _TS_COLOR, 2, cv2.LINE_AA,
                )

                ok, buf = cv2.imencode(".jpg", annotated, _JPEG_QUALITY)
                if ok:
                    captured_at = time.time()
                    classes = sorted({d["label"] for d in detections})
                    self._state.update(buf.tobytes(), classes, session_id, captured_at)
                    self._stats.record_frame(processing_ms, detections)
                    self._otel.frames_processed.add(1)
                    self._otel.frame_processing_ms.record(processing_ms)
                    for d in detections:
                        self._otel.detections_total.add(1, {"class": d["label"]})

                time.sleep(0.05)  # ~20 FPS target
        finally:
            tracker.force_end()
            recorder.stop()
            release_camera(cap)
            async_detector.close()
            logger.info("Camera released")


_state = _State()
_config = _DetectionConfig()
_stats = _AppStats()
_otel: Optional[AppMetrics] = None
_loop: Optional[DetectionLoop] = None


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Start telemetry and detection loop on startup; stop on shutdown."""
    global _otel, _loop
    _otel = setup_telemetry()
    setup_health_telemetry()
    _loop = DetectionLoop(_state, _config, _stats, _otel)
    _loop.start()
    yield
    _loop.stop()


app = FastAPI(title="Night Watcher", version=__version__, lifespan=_lifespan)


# ---------------------------------------------------------------------------
# Health endpoints
# ---------------------------------------------------------------------------


@app.get("/health", summary="Liveness probe")
def health() -> dict[str, str]:
    """Return service status."""
    return {"status": "ok"}


@app.get("/health/detailed", summary="Detailed system health")
def health_detailed() -> dict[str, Any]:
    """Return CPU, memory, disk, temperature and uptime for the host Pi."""
    return get_system_health()


@app.get("/health/docker", summary="Docker container status")
def health_docker() -> list[dict[str, str]]:
    """Return the list of Docker containers visible via the Docker socket."""
    return get_docker_services()


@app.get("/health/pmic", summary="Raspberry Pi 5 PMIC voltage and current readings")
def health_pmic() -> dict[str, Any]:
    """Return live voltage and current from all PMIC ADC channels.

    Requires ``vcgencmd`` and ``/dev/vcio`` inside the container.
    Key field: ``ext5v_v`` is the USB-C input voltage from your power supply.
    """
    return get_pmic_readings()


@app.get("/health/power", summary="Raspberry Pi power and throttle status")
def health_power() -> dict[str, Any]:
    """Return the Pi's current and historical throttle/under-voltage flags.

    Reads ``vcgencmd get_throttled`` (requires ``/dev/vcio`` and the
    ``vcgencmd`` binary to be accessible inside the container).  A ``healthy``
    value of ``true`` means no throttling or power issues have been detected
    since the last boot.
    """
    return get_power_status()


# ---------------------------------------------------------------------------
# Video stream endpoints
# ---------------------------------------------------------------------------


@app.get("/frame", summary="Latest annotated JPEG frame")
def get_frame() -> Response:
    """Return the most recent annotated camera frame as a JPEG image."""
    frame, _, _, _ = _state.snapshot()
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
            frame, _, _, _ = _state.snapshot()
            if frame:
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
            time.sleep(0.05)

    return StreamingResponse(
        _generate(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


# ---------------------------------------------------------------------------
# Detection state endpoints
# ---------------------------------------------------------------------------


@app.get("/status", summary="Current detection state")
def get_status() -> dict[str, Any]:
    """Return the active session ID, currently detected classes, and frame age."""
    _, classes, session_id, captured_at = _state.snapshot()
    frame_age_ms = round((time.time() - captured_at) * 1000) if captured_at else None
    return {
        "detecting": len(classes) > 0,
        "detection_active": _config.is_active(),
        "session_id": session_id,
        "detected_classes": classes,
        "frame_captured_at": captured_at,
        "frame_age_ms": frame_age_ms,
    }


# ---------------------------------------------------------------------------
# Metrics & logs endpoints
# ---------------------------------------------------------------------------


@app.get("/metrics/app", summary="Application runtime metrics")
def get_app_metrics() -> dict[str, Any]:
    """Return a JSON snapshot of frames processed, FPS, detection counts, etc."""
    return _stats.snapshot()


@app.get("/logs", summary="Recent structured log entries")
def get_logs(
    limit: int = Query(default=200, ge=1, le=1000),
    level: Optional[str] = Query(default=None),
    since: Optional[float] = Query(default=None),
) -> list[dict[str, Any]]:
    """Return recent application log entries stored in SQLite.

    Parameters
    ----------
    limit:
        Max entries to return (1–1000, default 200).
    level:
        Filter to a specific level: ``DEBUG``, ``INFO``, ``WARNING``,
        ``ERROR``, ``CRITICAL``.  Omit for all levels.
    since:
        UNIX timestamp; only return entries newer than this value.
    """
    return query_logs(limit=limit, level=level, since=since)


# ---------------------------------------------------------------------------
# Detection config endpoints
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Detection history & video endpoints
# ---------------------------------------------------------------------------


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
