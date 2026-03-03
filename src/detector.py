import cv2
import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from ultralytics import YOLO

ANIMAL_CLASSES = {
    "bird",
    "cat",
    "dog",
    "horse",
    "sheep",
    "cow",
    "elephant",
    "bear",
    "zebra",
    "giraffe",
}
TARGET_CLASSES = {"person"} | ANIMAL_CLASSES
logger = logging.getLogger("night_watcher.detector")


class YoloDetector:
    def __init__(self, model_name: str = "yolov8n.pt", conf_threshold: float = 0.35):
        self.model = YOLO(model_name)
        self.class_names = self.model.names
        self.conf_threshold = conf_threshold

    def detect(self, frame):
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
            detections.append(
                {
                    "label": label,
                    "confidence": confidence,
                    "bbox": (x1, y1, x2, y2),
                }
            )

        return detections

    def annotate(self, frame, detections):
        annotated_frame = frame.copy()

        for det in detections:
            x1, y1, x2, y2 = det["bbox"]
            label = f'{det["label"]} {det["confidence"]:.2f}'

            cv2.rectangle(annotated_frame, (x1, y1), (x2, y2), (0, 200, 255), 2)
            cv2.putText(
                annotated_frame,
                label,
                (x1, max(y1 - 10, 20)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (0, 200, 255),
                2,
                cv2.LINE_AA,
            )

        return annotated_frame

    def detect_and_annotate(self, frame):
        detections = self.detect(frame)
        annotated_frame = self.annotate(frame, detections)
        detection_flag = len(detections) > 0
        return annotated_frame, detection_flag, detections


class AsyncYoloDetector:
    """Runs detection in a single background worker so UI rendering stays responsive."""

    def __init__(self, detector: YoloDetector):
        self.detector = detector
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="yolo-detector")
        self._future: Future | None = None
        self._latest_detections = []
        self._lock = threading.Lock()

    def _poll_result(self) -> None:
        if self._future is None or not self._future.done():
            return
        try:
            detections = self._future.result()
        except Exception:
            logger.exception("YOLO async inference failed")
            detections = []
        with self._lock:
            self._latest_detections = detections
        self._future = None

    def process_frame(self, frame):
        self._poll_result()

        if self._future is None:
            # Submit latest frame; older frames are intentionally dropped.
            self._future = self._executor.submit(self.detector.detect, frame.copy())

        with self._lock:
            detections = list(self._latest_detections)

        annotated_frame = self.detector.annotate(frame, detections)
        detection_flag = len(detections) > 0
        return annotated_frame, detection_flag, detections

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
