import cv2
import streamlit as st

from src.camera import open_camera, read_frame, release_camera
from src.detector import YoloDetector
from src.utils import setup_logging

logger = setup_logging().getChild("app")


@st.cache_resource
def get_detector() -> YoloDetector:
    logger.info("Loading YOLO detector model")
    return YoloDetector(model_name="yolov8n.pt", conf_threshold=0.35)


def toggle_detection():
    st.session_state.detection_enabled = not st.session_state.detection_enabled


def main():
    logger.info("Starting Streamlit webcam app")
    st.title("Webcam Live Stream")

    if "detection_enabled" not in st.session_state:
        st.session_state.detection_enabled = False

    button_label = (
        "Disable YOLO Detection"
        if st.session_state.detection_enabled
        else "Enable YOLO Detection"
    )
    st.button(button_label, on_click=toggle_detection)
    st.write(f"Detection enabled: `{st.session_state.detection_enabled}`")

    detector = None
    if st.session_state.detection_enabled:
        try:
            detector = get_detector()
        except Exception:
            logger.exception("YOLO detector failed to load")
            st.error("Failed to load YOLO model. Check dependencies and model download.")
            return

    logger.info("Opening webcam device 0")
    cap = open_camera(0)
    if not cap.isOpened():
        logger.error("Cannot open camera device 0")
        st.error("Cannot open camera")
        return

    frame_placeholder = st.empty()
    status_placeholder = st.empty()
    frame_count = 0

    try:
        while True:
            ret, frame = read_frame(cap)
            if not ret:
                logger.error("Failed to capture frame from webcam")
                st.error("Failed to capture frame")
                break

            detection_flag = False
            detected_labels = []

            if detector is not None:
                frame, detection_flag, detections = detector.detect_and_annotate(frame)
                detected_labels = [d["label"] for d in detections]

            status_placeholder.write(
                f"detection_flag: `{detection_flag}` | detected: `{', '.join(detected_labels) or 'none'}`"
            )

            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            frame_placeholder.image(frame_rgb, channels="RGB")

            frame_count += 1
            if frame_count % 300 == 0:
                logger.info(
                    "Processed %d frames (detection_enabled=%s)",
                    frame_count,
                    st.session_state.detection_enabled,
                )
    finally:
        release_camera(cap)
        logger.info("Released webcam and exiting app loop (frames=%d)", frame_count)

if __name__ == "__main__":
    main()
