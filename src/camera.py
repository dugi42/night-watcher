import cv2


def open_camera(index: int = 0) -> cv2.VideoCapture:
    return cv2.VideoCapture(index)


def read_frame(cap: cv2.VideoCapture):
    return cap.read()


def release_camera(cap: cv2.VideoCapture) -> None:
    cap.release()
