import cv2
import json
import os
import streamlit as st
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import pandas as pd

from src.camera import configure_camera, list_video_devices, open_camera, read_frame, release_camera
from src.detector import AsyncYoloDetector, YoloDetector
from src.recorder import VideoRecorder
from src.tracker import DetectionTracker
from src.utils import setup_logging

logger = setup_logging().getChild("app")

ASSETS_DIR = Path(os.getenv("ASSETS_DIR", "/assets"))
META_FILE = ASSETS_DIR / "meta" / "detections.json"
VIDEO_DIR = ASSETS_DIR / "video"


@st.cache_resource
def get_detector() -> YoloDetector:
    model_path = os.getenv("YOLO_MODEL_PATH", "/app/models/yolov8n.pt")
    logger.info("Loading YOLO detector model from %s", model_path)
    return YoloDetector(model_name=model_path, conf_threshold=0.35)


def toggle_detection():
    st.session_state.detection_enabled = not st.session_state.detection_enabled


def initialize_state() -> None:
    defaults = {
        "detection_enabled": False,
        "streaming": False,
        "cap": None,
        "camera_error": None,
        "frame_count": 0,
        "async_detector": None,
        "tracker": None,
        "recorder": None,
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
    tracker: DetectionTracker | None = st.session_state.get("tracker")
    if tracker is not None:
        tracker.force_end()
    recorder: VideoRecorder | None = st.session_state.get("recorder")
    if recorder is not None:
        recorder.stop()
    release_camera(st.session_state.get("cap"))
    st.session_state.cap = None
    st.session_state.streaming = False
    logger.info("Released webcam stream")


def _ensure_tracker_recorder() -> None:
    """Lazily create tracker and recorder and wire them together."""
    if st.session_state.tracker is None:
        recorder = VideoRecorder()
        tracker = DetectionTracker()
        tracker.set_callbacks(on_start=recorder.start, on_end=recorder.stop)
        st.session_state.tracker = tracker
        st.session_state.recorder = recorder


def _render_stream_tab() -> None:
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

    async_detector = st.session_state.async_detector
    if st.session_state.detection_enabled:
        try:
            if st.session_state.async_detector is None:
                st.session_state.async_detector = AsyncYoloDetector(get_detector())
            async_detector = st.session_state.async_detector
            _ensure_tracker_recorder()
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
    detections = []

    if async_detector is not None:
        frame, detection_flag, detections = async_detector.process_frame(frame)
        detected_labels = sorted({d["label"] for d in detections})

    # Update tracker and feed recorder
    tracker: DetectionTracker | None = st.session_state.get("tracker")
    recorder: VideoRecorder | None = st.session_state.get("recorder")
    if tracker is not None:
        tracker.update(detections if st.session_state.detection_enabled else [])
    if recorder is not None and recorder.is_recording:
        recorder.write_frame(frame)

    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    frame_placeholder.image(frame_rgb, channels="RGB", output_format="JPEG")

    session_id = tracker.active_session_id if tracker else None
    st.session_state.frame_count += 1
    status_placeholder.write(
        f"detection_flag: `{detection_flag}` | "
        f"detected: `{', '.join(detected_labels) or 'none'}` | "
        f"recording: `{recorder.is_recording if recorder else False}` | "
        f"session: `{session_id[:8] + '...' if session_id else 'none'}` | "
        f"frames: `{st.session_state.frame_count}`"
    )

    if st.session_state.frame_count % 300 == 0:
        logger.info(
            "Processed %d frames (detection_enabled=%s)",
            st.session_state.frame_count,
            st.session_state.detection_enabled,
        )

    time.sleep(0.08)
    st.rerun()


def _render_stats_tab() -> None:
    st.header("Detection Statistics")

    if st.button("Refresh", key="stats_refresh"):
        st.rerun()

    if not META_FILE.exists():
        st.info("No detection data yet. Start the stream with detection enabled.")
        return

    try:
        sessions = json.loads(META_FILE.read_text())
    except Exception:
        st.error("Could not load detection data.")
        return

    if not sessions:
        st.info("No sessions recorded yet.")
        return

    all_objects = [o for s in sessions for o in s.get("objects", [])]
    class_counter = Counter(o["label"] for o in all_objects)
    total_duration = sum(s.get("duration_seconds", 0) for s in sessions)

    # Summary metrics
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Sessions", len(sessions))
    c2.metric("Total Recording Time", f"{total_duration:.0f}s")
    c3.metric("Unique Classes", len(class_counter))
    top_class = class_counter.most_common(1)[0][0] if class_counter else "—"
    c4.metric("Most Detected", top_class)

    st.divider()

    # Charts side by side
    col_l, col_r = st.columns(2)

    with col_l:
        st.subheader("Detections per Class")
        df_count = pd.DataFrame(
            class_counter.most_common(), columns=["Class", "Count"]
        ).set_index("Class")
        st.bar_chart(df_count)

    with col_r:
        st.subheader("Avg Detection Duration per Class (s)")
        dur_by_class: dict[str, list[float]] = {}
        for o in all_objects:
            dur_by_class.setdefault(o["label"], []).append(o.get("duration_seconds", 0.0))
        avg_dur = {k: round(sum(v) / len(v), 1) for k, v in dur_by_class.items()}
        df_dur = pd.DataFrame(
            sorted(avg_dur.items(), key=lambda x: -x[1]),
            columns=["Class", "Avg Duration (s)"],
        ).set_index("Class")
        st.bar_chart(df_dur)

    # Sessions per day chart
    if len(sessions) >= 2:
        st.subheader("Sessions per Day")
        day_counter: Counter = Counter()
        for s in sessions:
            day = datetime.fromtimestamp(s["start_time"]).strftime("%Y-%m-%d")
            day_counter[day] += 1
        df_day = pd.DataFrame(
            sorted(day_counter.items()), columns=["Date", "Sessions"]
        ).set_index("Date")
        st.bar_chart(df_day)

    st.divider()

    # Recent sessions
    st.subheader(f"Sessions (most recent first, total: {len(sessions)})")
    for session in reversed(sessions[-30:]):
        uid: str = session["uuid"]
        start_dt = datetime.fromtimestamp(session["start_time"])
        duration = session.get("duration_seconds", 0.0)
        classes = ", ".join(sorted({o["label"] for o in session.get("objects", [])}))

        with st.expander(f"{start_dt:%Y-%m-%d %H:%M:%S}  —  {classes}  ({duration:.1f}s)"):
            cols = st.columns([1, 2])
            with cols[0]:
                st.write(f"**UUID:** `{uid}`")
                end_ts = session.get("end_time")
                if end_ts:
                    end_dt = datetime.fromtimestamp(end_ts)
                    st.write(f"**Start:** {start_dt:%H:%M:%S}  →  **End:** {end_dt:%H:%M:%S}")
                st.write(f"**Duration:** {duration:.1f}s")

                obj_rows = [
                    {
                        "Class": o["label"],
                        "Duration (s)": round(o.get("duration_seconds", 0.0), 1),
                    }
                    for o in session.get("objects", [])
                ]
                if obj_rows:
                    st.dataframe(
                        pd.DataFrame(obj_rows), hide_index=True, use_container_width=True
                    )

            with cols[1]:
                video_path = VIDEO_DIR / f"{uid}.mp4"
                if video_path.exists():
                    st.video(str(video_path))
                else:
                    st.write("_No video recording available_")


def main():
    st.title("Night Watcher")
    initialize_state()

    tab_stream, tab_stats = st.tabs(["Live Stream", "Statistics"])

    with tab_stream:
        _render_stream_tab()

    with tab_stats:
        _render_stats_tab()


if __name__ == "__main__":
    main()
