from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from src import detector


class _Scalar:
    def __init__(self, value: float) -> None:
        self._value = value

    def item(self) -> float:
        return self._value


class _Coords:
    def __init__(self, values: list[float]) -> None:
        self._values = values

    def tolist(self) -> list[float]:
        return self._values


class _Box:
    def __init__(self, conf: float, cls_id: int, xyxy: list[float]) -> None:
        self.conf = _Scalar(conf)
        self.cls = _Scalar(cls_id)
        self.xyxy = [_Coords(xyxy)]


class _FakeModel:
    def __init__(self, _model_name: str) -> None:
        self.names = {0: "person", 1: "cat", 2: "car"}
        self._boxes = [
            _Box(0.9, 0, [1, 2, 30, 40]),
            _Box(0.8, 1, [4, 5, 20, 25]),
            _Box(0.2, 1, [0, 0, 1, 1]),
            _Box(0.95, 2, [3, 3, 7, 7]),
        ]

    def __call__(self, _frame, verbose: bool = False):
        return [SimpleNamespace(boxes=self._boxes)]


class _FakeFuture:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self._result = result
        self._error = error

    def done(self) -> bool:
        return True

    def result(self):
        if self._error is not None:
            raise self._error
        return self._result


class _FakeExecutor:
    def __init__(self, result) -> None:
        self.result = result
        self.shutdown_calls: list[tuple[bool, bool]] = []

    def submit(self, func, frame):
        return _FakeFuture(func(frame))

    def shutdown(self, wait: bool, cancel_futures: bool) -> None:
        self.shutdown_calls.append((wait, cancel_futures))


def test_yolo_detector_filters_targets_and_confidence(monkeypatch) -> None:
    monkeypatch.setattr(detector, "YOLO", _FakeModel)
    model = detector.YoloDetector(conf_threshold=0.35)

    detections = model.detect(np.zeros((10, 10, 3), dtype=np.uint8))

    assert detections == [
        {"label": "person", "confidence": 0.9, "bbox": (1, 2, 30, 40)},
        {"label": "cat", "confidence": 0.8, "bbox": (4, 5, 20, 25)},
    ]


def test_annotate_and_detect_and_annotate(monkeypatch) -> None:
    monkeypatch.setattr(detector, "YOLO", _FakeModel)
    rectangle_calls: list[tuple] = []
    text_calls: list[tuple] = []
    monkeypatch.setattr(detector.cv2, "rectangle", lambda *args: rectangle_calls.append(args))
    monkeypatch.setattr(detector.cv2, "putText", lambda *args: text_calls.append(args))

    model = detector.YoloDetector(conf_threshold=0.35)
    frame = np.zeros((12, 12, 3), dtype=np.uint8)
    annotated, found, detections = model.detect_and_annotate(frame)

    assert annotated is not frame
    assert found is True
    assert len(detections) == 2
    assert len(rectangle_calls) == 2
    assert len(text_calls) == 2


def test_async_yolo_detector_processes_latest_result_and_handles_errors(monkeypatch) -> None:
    class _StubDetector:
        def detect(self, frame):
            return [{"label": "dog", "confidence": 0.7, "bbox": (0, 0, 1, 1), "frame_sum": int(frame.sum())}]

        def annotate(self, frame, detections):
            return {"shape": frame.shape, "detections": list(detections)}

    async_detector = detector.AsyncYoloDetector(_StubDetector())
    async_detector._executor = _FakeExecutor([{"label": "dog", "confidence": 0.7, "bbox": (0, 0, 1, 1)}])

    frame = np.ones((4, 4, 3), dtype=np.uint8)
    annotated_1, found_1, detections_1 = async_detector.process_frame(frame)
    annotated_2, found_2, detections_2 = async_detector.process_frame(frame)

    assert annotated_1["detections"] == []
    assert found_1 is False
    assert detections_1 == []
    assert annotated_2["detections"][0]["label"] == "dog"
    assert found_2 is True
    assert detections_2[0]["label"] == "dog"

    async_detector._future = _FakeFuture(error=RuntimeError("boom"))
    async_detector._collect_result()
    assert async_detector._future is None
    assert async_detector._latest_detections == []

    async_detector.close()
    assert async_detector._executor.shutdown_calls == [(False, True)]
