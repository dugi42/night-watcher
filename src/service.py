"""FastAPI detection service — runs on the Raspberry Pi inside Docker.

Opens the camera on startup, runs YOLO object detection continuously in a
background thread, feeds the tracker and recorder, and exposes HTTP endpoints
so the Streamlit client can consume live video and detection history.

Endpoints
---------
GET /health         Liveness probe.
GET /frame          Latest annotated JPEG frame (for polling clients).
GET /stream         MJPEG video stream (for browser <img> tags).
GET /status         Current detection state as JSON.
GET /detections     Full history of recorded sessions from detections.json.
GET /video/{uuid}   Serve a recorded MP4 by session UUID.
"""

import json
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import cv2
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response, StreamingResponse

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


class DetectionLoop:
    """Runs the camera capture and YOLO inference loop in a daemon thread.

    On start, opens the camera, loads the YOLO model, and continuously reads
    frames. Each frame is passed to the async detector; results feed the
    tracker (session lifecycle) and recorder (video writing). The latest
    annotated JPEG is stored in shared state for the API to serve.
    """

    def __init__(self, state: _State) -> None:
        self._state = state
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
        model_path = os.getenv("YOLO_MODEL_PATH", "/app/models/yolov8n.pt")
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

        try:
            while self._running:
                ret, frame = read_frame(cap)
                if not ret:
                    logger.warning("Failed to read frame; retrying")
                    time.sleep(0.1)
                    continue

                annotated, _, detections = async_detector.process_frame(frame)

                session_id = tracker.update(detections)
                if recorder.is_recording:
                    recorder.write_frame(frame)

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
_loop = DetectionLoop(_state)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Start the detection loop on startup; stop it on shutdown."""
    _loop.start()
    yield
    _loop.stop()


app = FastAPI(title="Night Watcher", version="1.0.0", lifespan=_lifespan)


@app.get("/health", summary="Liveness probe")
def health() -> dict:
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

    def _generate():
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
def get_status() -> dict:
    """Return the active session ID and currently detected classes."""
    _, classes, session_id = _state.snapshot()
    return {
        "detecting": len(classes) > 0,
        "session_id": session_id,
        "detected_classes": classes,
    }


@app.get("/detections", summary="All recorded detection sessions")
def get_detections() -> list:
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
