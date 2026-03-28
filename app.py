"""Streamlit client for Night Watcher — runs on your local machine.

Connects to the FastAPI detection service running on the Raspberry Pi and
displays a live MJPEG stream alongside detection statistics and recorded video
clips. Configure the Pi URL via the RASPI_URL environment variable or the
sidebar input.

Usage
-----
    RASPI_URL=http://raspi.local:8000 streamlit run app.py
"""

import os
from collections import Counter
from datetime import datetime, time as dt_time

import pandas as pd
import requests
import streamlit as st

DEFAULT_URL = os.getenv("RASPI_URL", "http://raspi.local:8000")


def _post(path: str, base_url: str, payload: dict, timeout: float = 3.0):
    """Perform a POST request against the Pi service and return the response."""
    try:
        resp = requests.post(f"{base_url}{path}", json=payload, timeout=timeout)
        resp.raise_for_status()
        return resp
    except requests.RequestException:
        return None


def _get(path: str, base_url: str, timeout: float = 3.0):
    """Perform a GET request against the Pi service and return the response.

    Returns None on any network or HTTP error, avoiding noisy tracebacks in
    the UI.
    """
    try:
        resp = requests.get(f"{base_url}{path}", timeout=timeout)
        resp.raise_for_status()
        return resp
    except requests.RequestException:
        return None


def _render_sidebar() -> str:
    """Render the sidebar and return the configured Pi base URL."""
    with st.sidebar:
        st.image("assets/logo.jpeg", use_container_width=True)
        st.title("Night Watcher")
        url = st.text_input("Pi service URL", value=DEFAULT_URL)

        resp = _get("/health", url, timeout=2.0)
        if resp is not None:
            st.success("Pi reachable")
        else:
            st.error("Pi unreachable")

        st.divider()
        st.subheader("Detection Control")

        cfg_resp = _get("/detection/config", url, timeout=2.0)
        if cfg_resp is None:
            st.warning("Could not fetch detection config.")
        else:
            cfg = cfg_resp.json()

            enabled = st.toggle("Detection Enabled", value=cfg.get("enabled", True))

            schedule_enabled = st.toggle(
                "Use Time Schedule",
                value=cfg.get("schedule_enabled", False),
                disabled=not enabled,
            )

            start_time: dt_time = dt_time(20, 0)
            end_time: dt_time = dt_time(6, 0)
            if schedule_enabled and enabled:
                col1, col2 = st.columns(2)
                with col1:
                    h, m = map(int, cfg.get("schedule_start", "20:00").split(":"))
                    start_time = st.time_input("Active from", value=dt_time(h, m))
                with col2:
                    h, m = map(int, cfg.get("schedule_end", "06:00").split(":"))
                    end_time = st.time_input("Active until", value=dt_time(h, m))

            if st.button("Apply", type="primary"):
                _post("/detection/config", url, {
                    "enabled": enabled,
                    "schedule_enabled": schedule_enabled,
                    "schedule_start": start_time.strftime("%H:%M"),
                    "schedule_end": end_time.strftime("%H:%M"),
                })
                st.rerun()

    return url.rstrip("/")


def _render_stream_tab(url: str) -> None:
    """Render the live MJPEG stream and current detection status.

    The stream is embedded via an HTML <img> tag pointing directly at the
    Pi's MJPEG endpoint; the browser handles continuous updates without
    Streamlit needing to poll for frames.
    """
    stream_url = f"{url}/stream"

    st.components.v1.html(
        f"""
        <img
            src="{stream_url}"
            style="width:100%;height:auto;border-radius:6px;"
            onerror="this.alt='Stream unavailable — is the Pi running?'"
        />
        """,
        height=500,
    )

    resp = _get("/status", url)
    if resp is not None:
        status = resp.json()
        classes = ", ".join(status.get("detected_classes", [])) or "none"
        sid = status.get("session_id") or ""
        detection_active = status.get("detection_active", True)
        col1, col2, col3 = st.columns(3)
        col1.metric("Detection", "Active" if detection_active else "Paused")
        col2.metric("Detected", classes)
        col3.metric("Session", sid[:8] + "…" if sid else "—")
        if not detection_active:
            st.info("Detection is currently paused. Enable it in the sidebar.")
    else:
        st.warning("Could not fetch status from Pi.")


def _render_stats_tab(url: str) -> None:
    """Render detection statistics and recorded session clips.

    Fetches the full session history from the Pi's /detections endpoint and
    displays summary metrics, per-class bar charts, and an expandable list of
    recent sessions with inline video playback.
    """
    st.header("Detection Statistics")

    if st.button("Refresh"):
        st.rerun()

    resp = _get("/detections", url, timeout=5.0)
    if resp is None:
        st.error("Could not fetch detection history from Pi.")
        return

    sessions: list[dict] = resp.json()
    if not sessions:
        st.info("No detection sessions recorded yet.")
        return

    all_objects = [o for s in sessions for o in s.get("objects", [])]
    class_counter = Counter(o["label"] for o in all_objects)
    total_duration = sum(s.get("duration_seconds", 0.0) for s in sessions)
    top_class = class_counter.most_common(1)[0][0] if class_counter else "—"

    # Summary metrics
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Sessions", len(sessions))
    c2.metric("Total Time", f"{total_duration:.0f}s")
    c3.metric("Unique Classes", len(class_counter))
    c4.metric("Most Detected", top_class)

    st.divider()

    col_l, col_r = st.columns(2)

    with col_l:
        st.subheader("Detections per Class")
        df_count = pd.DataFrame(
            class_counter.most_common(), columns=["Class", "Count"]
        ).set_index("Class")
        st.bar_chart(df_count)

    with col_r:
        st.subheader("Avg Duration per Class (s)")
        dur_by_class: dict[str, list[float]] = {}
        for o in all_objects:
            dur_by_class.setdefault(o["label"], []).append(o.get("duration_seconds", 0.0))
        avg_dur = {k: round(sum(v) / len(v), 1) for k, v in dur_by_class.items()}
        df_dur = pd.DataFrame(
            sorted(avg_dur.items(), key=lambda x: -x[1]),
            columns=["Class", "Avg Duration (s)"],
        ).set_index("Class")
        st.bar_chart(df_dur)

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
    st.subheader(f"Recent Sessions (showing last 30 of {len(sessions)})")

    for session in reversed(sessions[-30:]):
        uid: str = session["uuid"]
        start_dt = datetime.fromtimestamp(session["start_time"])
        duration = session.get("duration_seconds", 0.0)
        classes = ", ".join(sorted({o["label"] for o in session.get("objects", [])}))

        with st.expander(f"{start_dt:%Y-%m-%d %H:%M:%S}  —  {classes}  ({duration:.1f}s)"):
            left, right = st.columns([1, 2])

            with left:
                st.write(f"**UUID:** `{uid}`")
                end_ts = session.get("end_time")
                end_dt = datetime.fromtimestamp(end_ts) if end_ts else None
                st.write(f"**Start:** {start_dt:%H:%M:%S}" + (f"  →  **End:** {end_dt:%H:%M:%S}" if end_dt else ""))
                st.write(f"**Duration:** {duration:.1f}s")

                obj_rows = [
                    {"Class": o["label"], "Duration (s)": round(o.get("duration_seconds", 0.0), 1)}
                    for o in session.get("objects", [])
                ]
                if obj_rows:
                    st.dataframe(pd.DataFrame(obj_rows), hide_index=True, use_container_width=True)

            with right:
                video_url = f"{url}/video/{uid}"
                # Streamlit's st.video supports URLs directly
                st.video(video_url)


def main() -> None:
    """Entry point for the Streamlit client application."""
    st.set_page_config(
        page_title="Night Watcher",
        page_icon="🦉",
        layout="wide",
    )

    url = _render_sidebar()

    tab_stream, tab_stats = st.tabs(["📷 Live Stream", "📊 Statistics"])

    with tab_stream:
        _render_stream_tab(url)

    with tab_stats:
        _render_stats_tab(url)


if __name__ == "__main__":
    main()
