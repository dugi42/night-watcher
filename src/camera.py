import cv2
import glob
import os
import threading
import time

_CAMERA_LOCK = threading.Lock()
_SHARED_CAP = None


def list_video_devices() -> list[str]:
    return sorted(glob.glob("/dev/video*"))


def _camera_sources(index: int = 0) -> list[object]:
    preferred = os.getenv("CAMERA_DEVICE", f"/dev/video{index}")
    sources = [preferred, index, f"/dev/video{index}"]
    deduped = []
    for source in sources:
        if source not in deduped:
            deduped.append(source)
    return deduped


def open_camera(index: int = 0, retries: int = 4, retry_delay: float = 0.3):
    global _SHARED_CAP

    with _CAMERA_LOCK:
        if _SHARED_CAP is not None and _SHARED_CAP.isOpened():
            return _SHARED_CAP

        for _ in range(retries):
            for source in _camera_sources(index):
                cap = cv2.VideoCapture(source, cv2.CAP_V4L2)
                if cap is not None and cap.isOpened():
                    _SHARED_CAP = cap
                    return cap
                if cap is not None:
                    cap.release()
            time.sleep(retry_delay)

    return None


def configure_camera(cap: cv2.VideoCapture, width: int = 640, height: int = 480, fps: int = 20) -> None:
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    cap.set(cv2.CAP_PROP_FPS, fps)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)


def read_frame(cap: cv2.VideoCapture):
    return cap.read()


def release_camera(cap: cv2.VideoCapture) -> None:
    global _SHARED_CAP

    with _CAMERA_LOCK:
        if cap is not None:
            cap.release()
            if cap is _SHARED_CAP:
                _SHARED_CAP = None
