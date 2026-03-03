import cv2
import streamlit as st
import time

from src.camera import configure_camera, list_video_devices, open_camera, read_frame, release_camera
from src.detector import YoloDetector
from src.utils import setup_logging

logger = setup_logging().getChild("app")


@st.cache_resource
def get_detector() -> YoloDetector:
    logger.info("Loading YOLO detector model")
    return YoloDetector(model_name="yolov8n.pt", conf_threshold=0.35)


def toggle_detection():
    st.session_state.detection_enabled = not st.session_state.detection_enabled


def initialize_state() -> None:
    defaults = {
        "detection_enabled": False,
        "streaming": False,
        "cap": None,
        "camera_error": None,
        "frame_count": 0,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def start_stream() -> None:
    cap = st.session_state.get("cap")
    if cap is not None and cap.isOpened():
        st.session_state.streaming = True
        st.session_state.camera_error = None
        return

    logger.info("Opening webcam device 0")
    cap = open_camera(0)
    if cap is None or not cap.isOpened():
        devices = list_video_devices()
        logger.error("Cannot open camera device 0 (devices in container: %s)", devices)
        st.session_state.streaming = False
        st.session_state.camera_error = (
            "Cannot open camera device 0. "
            f"Available devices in container: {devices or 'none'}"
        )
        release_camera(cap)
        st.session_state.cap = None
        return

    configure_camera(cap, width=640, height=480, fps=20)
    st.session_state.cap = cap
    st.session_state.streaming = True
    st.session_state.camera_error = None
    st.session_state.frame_count = 0


def stop_stream() -> None:
    release_camera(st.session_state.get("cap"))
    st.session_state.cap = None
    st.session_state.streaming = False
    logger.info("Released webcam stream")


def main():
    st.title("Webcam Live Stream")
    initialize_state()

    controls = st.columns(3)
    controls[0].button(
        "Start Stream",
        on_click=start_stream,
        disabled=st.session_state.streaming,
        use_container_width=True,
    )
    controls[1].button(
        "Stop Stream",
        on_click=stop_stream,
        disabled=not st.session_state.streaming,
        use_container_width=True,
    )
    controls[2].button(
        "Toggle Detection",
        on_click=toggle_detection,
        use_container_width=True,
    )
    st.write(f"Detection enabled: `{st.session_state.detection_enabled}`")

    detector = None
    if st.session_state.detection_enabled:
        try:
            detector = get_detector()
        except Exception:
            logger.exception("YOLO detector failed to load")
            st.error("Failed to load YOLO model. Check dependencies and model download.")
            return

    if st.session_state.camera_error:
        st.error(st.session_state.camera_error)

    frame_placeholder = st.empty()
    status_placeholder = st.empty()

    if not st.session_state.streaming:
        status_placeholder.write("Stream is stopped. Click `Start Stream`.")
        return

    cap = st.session_state.get("cap")
    if cap is None or not cap.isOpened():
        st.session_state.streaming = False
        st.session_state.camera_error = "Camera handle is unavailable. Click `Start Stream` again."
        st.error(st.session_state.camera_error)
        return

    ret, frame = read_frame(cap)
    if not ret:
        logger.error("Failed to capture frame from webcam")
        st.session_state.camera_error = "Failed to capture frame from webcam."
        stop_stream()
        st.error(st.session_state.camera_error)
        return

    detection_flag = False
    detected_labels = []
    if detector is not None:
        frame, detection_flag, detections = detector.detect_and_annotate(frame)
        detected_labels = sorted({d["label"] for d in detections})

    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    frame_placeholder.image(frame_rgb, channels="RGB", output_format="JPEG")

    st.session_state.frame_count += 1
    status_placeholder.write(
        f"detection_flag: `{detection_flag}` | detected: `{', '.join(detected_labels) or 'none'}` | frames: `{st.session_state.frame_count}`"
    )

    if st.session_state.frame_count % 300 == 0:
        logger.info(
            "Processed %d frames (detection_enabled=%s)",
            st.session_state.frame_count,
            st.session_state.detection_enabled,
        )

    time.sleep(0.08)
    st.rerun()

if __name__ == "__main__":
    main()
