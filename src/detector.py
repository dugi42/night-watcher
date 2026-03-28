"""YOLO-based object detector for Night Watcher.

Provides a synchronous :class:`YoloDetector` and a non-blocking
:class:`AsyncYoloDetector` that runs inference in a background thread so the
capture loop is never stalled waiting for the GPU/CPU.

Only a curated set of animal and person classes are reported; everything else
is silently filtered.
"""

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

import cv2
import numpy as np
from ultralytics import YOLO

#: Animal classes from the COCO dataset that Night Watcher cares about.
ANIMAL_CLASSES = {
    "bird", "cat", "dog", "horse", "sheep",
    "cow", "elephant", "bear", "zebra", "giraffe",
}

#: Full set of classes that trigger a detection event.
TARGET_CLASSES = {"person"} | ANIMAL_CLASSES

logger = logging.getLogger("night_watcher.detector")


class YoloDetector:
    """Synchronous YOLO detector that filters results to target classes.

    Parameters
    ----------
    model_name:
        Path to a YOLO weights file or a model identifier accepted by
        :mod:`ultralytics` (e.g. ``"yolov8n.pt"``).
    conf_threshold:
        Minimum confidence score to include a detection. Defaults to 0.35.
    """

    def __init__(self, model_name: str = "yolov8n.pt", conf_threshold: float = 0.35) -> None:
        self.model = YOLO(model_name)
        self.class_names = self.model.names
        self.conf_threshold = conf_threshold

    def detect(self, frame: np.ndarray) -> list[dict[str, Any]]:
        """Run inference on *frame* and return filtered detections.

        Parameters
        ----------
        frame:
            BGR NumPy array.

        Returns
        -------
        list[dict]
            Each dict has keys ``"label"`` (str), ``"confidence"`` (float),
            and ``"bbox"`` (tuple of four ints: x1, y1, x2, y2).
        """
        result = self.model(frame, verbose=False)[0]
        detections = []

        for box in result.boxes:
            confidence = float(box.conf.item())
            if confidence < self.conf_threshold:
                continue

            class_id = int(box.cls.item())
            label = self.class_names[class_id]
            if label not in TARGET_CLASSES:
                continue

            x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
            detections.append({"label": label, "confidence": confidence, "bbox": (x1, y1, x2, y2)})

        return detections

    def annotate(self, frame: np.ndarray, detections: list[dict[str, Any]]) -> np.ndarray:
        """Draw bounding boxes and labels onto a copy of *frame*.

        Parameters
        ----------
        frame:
            Original BGR NumPy array; not modified in place.
        detections:
            Output of :meth:`detect`.

        Returns
        -------
        numpy.ndarray
            Annotated BGR frame.
        """
        annotated = frame.copy()
        for det in detections:
            x1, y1, x2, y2 = det["bbox"]
            label = f'{det["label"]} {det["confidence"]:.2f}'
            cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 200, 255), 2)
            cv2.putText(
                annotated, label, (x1, max(y1 - 10, 20)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 200, 255), 2, cv2.LINE_AA,
            )
        return annotated

    def detect_and_annotate(self, frame: np.ndarray) -> tuple[np.ndarray, bool, list[dict[str, Any]]]:
        """Convenience method combining :meth:`detect` and :meth:`annotate`.

        Returns
        -------
        tuple[numpy.ndarray, bool, list[dict]]
            ``(annotated_frame, detection_flag, detections)``
        """
        detections = self.detect(frame)
        return self.annotate(frame, detections), len(detections) > 0, detections


class AsyncYoloDetector:
    """Non-blocking wrapper around :class:`YoloDetector`.

    Inference runs in a single background worker thread. The capture loop
    calls :meth:`process_frame` every iteration; the method submits the
    latest frame for inference (dropping older ones) and immediately returns
    the annotated frame using the most recent completed detections — so the
    UI never blocks on the model.

    Parameters
    ----------
    detector:
        A configured :class:`YoloDetector` instance.
    """

    def __init__(self, detector: YoloDetector) -> None:
        self.detector = detector
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="yolo")
        self._future: Future[list[dict[str, Any]]] | None = None
        self._latest_detections: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def process_frame(self, frame: np.ndarray) -> tuple[np.ndarray, bool, list[dict[str, Any]]]:
        """Submit *frame* for inference and return the latest annotated result.

        Frames are intentionally dropped when the worker is busy — only the
        most recent frame is ever queued.

        Parameters
        ----------
        frame:
            BGR NumPy array from the camera.

        Returns
        -------
        tuple[numpy.ndarray, bool, list[dict]]
            ``(annotated_frame, detection_flag, detections)``
        """
        self._collect_result()

        if self._future is None:
            self._future = self._executor.submit(self.detector.detect, frame.copy())

        with self._lock:
            detections = list(self._latest_detections)

        annotated = self.detector.annotate(frame, detections)
        return annotated, len(detections) > 0, detections

    def close(self) -> None:
        """Shut down the background executor."""
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _collect_result(self) -> None:
        """Poll the pending future and store its result if ready."""
        if self._future is None or not self._future.done():
            return
        try:
            detections = self._future.result()
        except Exception:
            logger.exception("Async inference failed")
            detections = []
        with self._lock:
            self._latest_detections = detections
        self._future = None
