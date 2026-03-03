import cv2
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

    def detect_and_annotate(self, frame):
        detections = self.detect(frame)
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

        detection_flag = len(detections) > 0
        return annotated_frame, detection_flag, detections
