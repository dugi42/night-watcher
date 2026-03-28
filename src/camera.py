"""Camera access helpers for Night Watcher.

Wraps OpenCV's VideoCapture with retry logic, V4L2 backend selection, and a
global lock that prevents concurrent access to the same physical device.

The preferred camera device is read from the ``CAMERA_DEVICE`` environment
variable; it falls back to ``/dev/video{index}``.
"""

import glob
import logging
import os
import threading
import time

import cv2

logger = logging.getLogger("night_watcher.camera")

_CAMERA_LOCK = threading.Lock()
_SHARED_CAP = None


def list_video_devices() -> list[str]:
    """Return a sorted list of available V4L2 video device paths.

    Returns
    -------
    list[str]
        Paths such as ``["/dev/video0", "/dev/video1"]``.
    """
    return sorted(glob.glob("/dev/video*"))


def open_camera(index: int = 0, retries: int = 4, retry_delay: float = 0.3):
    """Open the camera and return a shared :class:`cv2.VideoCapture` handle.

    Returns the existing handle if the camera is already open. Tries each
    source in priority order (env var, numeric index, /dev/videoN) up to
    *retries* times before giving up.

    Parameters
    ----------
    index:
        Device index used to build the fallback ``/dev/video{index}`` path.
    retries:
        Number of full retry cycles across all sources.
    retry_delay:
        Seconds to wait between retry cycles.

    Returns
    -------
    cv2.VideoCapture | None
        An opened capture object, or ``None`` if every attempt failed.
    """
    global _SHARED_CAP

    with _CAMERA_LOCK:
        if _SHARED_CAP is not None and _SHARED_CAP.isOpened():
            return _SHARED_CAP

        for attempt in range(retries):
            for source in _camera_sources(index):
                cap = cv2.VideoCapture(source, cv2.CAP_V4L2)
                if cap is not None and cap.isOpened():
                    _SHARED_CAP = cap
                    logger.info("Camera opened: %s (attempt %d)", source, attempt + 1)
                    return cap
                if cap is not None:
                    cap.release()
            time.sleep(retry_delay)

    logger.error("Failed to open camera after %d retries (devices: %s)", retries, list_video_devices())
    return None


def configure_camera(
    cap: cv2.VideoCapture,
    width: int = 640,
    height: int = 480,
    fps: int = 20,
) -> None:
    """Apply resolution, frame rate, and buffer settings to an open capture.

    Sets ``BUFFERSIZE`` to 1 to minimise capture latency.

    Parameters
    ----------
    cap:
        An opened :class:`cv2.VideoCapture` instance.
    width:
        Requested frame width in pixels.
    height:
        Requested frame height in pixels.
    fps:
        Requested capture frame rate.
    """
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    cap.set(cv2.CAP_PROP_FPS, fps)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)


def read_frame(cap: cv2.VideoCapture) -> tuple[bool, object]:
    """Read a single frame from *cap*.

    Parameters
    ----------
    cap:
        An opened :class:`cv2.VideoCapture` instance.

    Returns
    -------
    tuple[bool, numpy.ndarray | None]
        ``(success, frame)`` as returned by :meth:`cv2.VideoCapture.read`.
    """
    return cap.read()


def release_camera(cap: cv2.VideoCapture | None) -> None:
    """Release *cap* and clear the shared handle if it matches.

    Safe to call with ``None`` or an already-released capture.

    Parameters
    ----------
    cap:
        The capture object to release, or ``None``.
    """
    global _SHARED_CAP

    with _CAMERA_LOCK:
        if cap is not None:
            cap.release()
            if cap is _SHARED_CAP:
                _SHARED_CAP = None
                logger.info("Shared camera handle released")


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _camera_sources(index: int) -> list[str | int]:
    """Build a deduplicated priority list of camera sources.

    Parameters
    ----------
    index:
        Numeric device index used as a fallback source.

    Returns
    -------
    list
        Sources to try in order: env-var path, numeric index, /dev/videoN.
    """
    preferred = os.getenv("CAMERA_DEVICE", f"/dev/video{index}")
    candidates = [preferred, index, f"/dev/video{index}"]
    seen: list[str | int] = []
    for src in candidates:
        if src not in seen:
            seen.append(src)
    return seen
