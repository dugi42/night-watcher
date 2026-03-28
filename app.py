"""Streamlit client for Night Watcher — runs on your local machine.

Connects to the FastAPI detection service running on the Raspberry Pi and
displays a live MJPEG stream alongside detection statistics and recorded video
clips. Configure the Pi URL via the RASPI_URL environment variable or the
sidebar input.

Usage
-----
    RASPI_URL=http://raspi.local:8000 streamlit run app.py
"""

import logging
import os
from collections import Counter
from datetime import datetime, time as dt_time

import pandas as pd
import requests
import streamlit as st

DEFAULT_URL = os.getenv("RASPI_URL", "http://raspi.local:8000")

logger = logging.getLogger("night_watcher.client")


def _post(path: str, base_url: str, payload: dict, timeout: float = 3.0):
    """Perform a POST request against the Pi service and return the response.

    Parameters
    ----------
    path:
        API path relative to *base_url*, e.g. ``"/detection/config"``.
    base_url:
        Base URL of the Pi service, e.g. ``"http://raspi.local:8000"``.
    payload:
        JSON-serializable dict sent as the request body.
    timeout:
        Seconds before the request is abandoned. Defaults to 3.0.

    Returns
    -------
    requests.Response | None
        The response on success, or ``None`` on any network or HTTP error.
    """
    url = f"{base_url}{path}"
    logger.debug("POST %s payload=%s", url, payload)
    try:
        resp = requests.post(url, json=payload, timeout=timeout)
        resp.raise_for_status()
        logger.debug("POST %s → %s", url, resp.status_code)
        return resp
    except requests.RequestException as exc:
        logger.error("POST %s failed: %s", url, exc)
        return None


def _get(path: str, base_url: str, timeout: float = 3.0):
    """Perform a GET request against the Pi service and return the response.

    Parameters
    ----------
    path:
        API path relative to *base_url*, e.g. ``"/status"``.
    base_url:
        Base URL of the Pi service.
    timeout:
        Seconds before the request is abandoned. Defaults to 3.0.

    Returns
    -------
    requests.Response | None
        The response on success, or ``None`` on any network or HTTP error.
        Returns ``None`` rather than raising to keep the UI stable when the
        Pi is temporarily unreachable.
    """
    url = f"{base_url}{path}"
    logger.debug("GET %s", url)
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        return resp
    except requests.RequestException as exc:
        logger.warning("GET %s failed: %s", url, exc)
        return None


def _render_sidebar() -> str:
    """Render the sidebar and return the configured Pi base URL.

    Displays the logo, URL input, connectivity indicator, and the Detection
    Control panel (enable toggle, optional time schedule, Apply button with
    success/error feedback).

    Returns
    -------
    str
        The Pi service base URL with any trailing slash stripped.
    """
    with st.sidebar:
        st.image("assets/logo.jpeg", use_container_width=True)
        st.title("Night Watcher")
        url = st.text_input("Pi service URL", value=DEFAULT_URL)

        resp = _get("/health", url, timeout=2.0)
        if resp is not None:
            st.success(f"Pi reachable — HTTP {resp.status_code}")
            logger.debug("Health check OK: %s", url)
        else:
            st.error("Pi unreachable — no response")
            logger.warning("Health check failed: %s", url)

        st.divider()
        st.subheader("Detection Control")

        # Display one-shot feedback from the previous Apply click
        if "cfg_feedback" in st.session_state:
            fb_type, fb_msg = st.session_state.pop("cfg_feedback")
            if fb_type == "success":
                st.success(fb_msg)
            else:
                st.error(fb_msg)

        cfg_resp = _get("/detection/config", url, timeout=2.0)
        if cfg_resp is None:
            st.warning("Could not fetch detection config.")
            logger.warning("GET /detection/config failed for %s", url)
        else:
            cfg = cfg_resp.json()
            st.caption(f"Config loaded — HTTP {cfg_resp.status_code}")
            logger.debug("Detection config: %s", cfg)

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

            conf_threshold = st.slider(
                "Confidence Threshold",
                min_value=0.10,
                max_value=0.90,
                value=float(cfg.get("conf_threshold", 0.35)),
                step=0.05,
                disabled=not enabled,
                help="Minimum YOLO confidence score for a detection to be recorded.",
            )

            if st.button("Apply", type="primary"):
                payload = {
                    "enabled": enabled,
                    "schedule_enabled": schedule_enabled,
                    "schedule_start": start_time.strftime("%H:%M"),
                    "schedule_end": end_time.strftime("%H:%M"),
                    "conf_threshold": conf_threshold,
                }
                logger.info("Applying detection config: %s", payload)
                result = _post("/detection/config", url, payload)

                if result is not None:
                    cfg_back = result.json()
                    active_str = "active now" if cfg_back.get("active") else "inactive now"
                    if not cfg_back["enabled"]:
                        msg = f"Detection disabled — HTTP {result.status_code}"
                    elif cfg_back["schedule_enabled"]:
                        msg = (
                            f"Schedule set: {cfg_back['schedule_start']} → "
                            f"{cfg_back['schedule_end']} ({active_str})"
                            f" — HTTP {result.status_code}"
                        )
                    else:
                        msg = (
                            f"Detection enabled, threshold {cfg_back['conf_threshold']:.0%}"
                            f" ({active_str}) — HTTP {result.status_code}"
                        )
                    st.session_state["cfg_feedback"] = ("success", msg)
                    logger.info("Detection config accepted by Pi: %s", cfg_back)
                else:
                    st.session_state["cfg_feedback"] = (
                        "error",
                        "Failed to reach Pi — config not applied",
                    )
                    logger.error("POST /detection/config failed: Pi unreachable at %s", url)

                st.rerun()

    return url.rstrip("/")


def _render_stream_tab(url: str) -> None:
    """Render the live MJPEG stream and current detection status metrics.

    The stream is embedded via an HTML ``<img>`` tag pointing directly at the
    Pi's MJPEG endpoint; the browser handles continuous updates without
    Streamlit needing to poll for frames. The wrapper ``<div>`` has
    ``resize: both`` so the user can drag it to any size.

    Below the stream, metrics from ``/status`` show whether detection is
    active, what classes are currently visible, and the active session ID.

    Parameters
    ----------
    url:
        Pi service base URL, e.g. ``"http://raspi.local:8000"``.
    """
    stream_url = f"{url}/stream"
    logger.debug("Rendering stream from %s", stream_url)

    st.components.v1.html(
        f"""
        <style>
          #stream-wrapper {{
            resize: both;
            overflow: hidden;
            width: 100%;
            height: 480px;
            min-width: 200px;
            min-height: 150px;
            border-radius: 6px;
            box-sizing: border-box;
            position: relative;
          }}
          #stream-wrapper img {{
            width: 100%;
            height: 100%;
            object-fit: contain;
            display: block;
            border-radius: 6px;
          }}
          /* Subtle resize-handle hint */
          #stream-wrapper::after {{
            content: "";
            position: absolute;
            bottom: 4px;
            right: 4px;
            width: 12px;
            height: 12px;
            background: linear-gradient(
              135deg,
              transparent 40%,
              rgba(255,255,255,0.5) 40%,
              rgba(255,255,255,0.5) 55%,
              transparent 55%,
              transparent 65%,
              rgba(255,255,255,0.5) 65%
            );
            pointer-events: none;
          }}
        </style>
        <div id="stream-wrapper">
          <img
            src="{stream_url}"
            onerror="this.alt='Stream unavailable — is the Pi running?'"
          />
        </div>
        """,
        height=820,
        scrolling=False,
    )

    resp = _get("/status", url)
    if resp is not None:
        status = resp.json()
        classes = ", ".join(status.get("detected_classes", [])) or "none"
        sid = status.get("session_id") or ""
        detection_active = status.get("detection_active", True)
        logger.debug(
            "Status: detection_active=%s classes=%s session=%s",
            detection_active, classes, sid,
        )
        col1, col2, col3 = st.columns(3)
        col1.metric("Detection", "Active" if detection_active else "Paused")
        col2.metric("Detected", classes)
        col3.metric("Session", sid[:8] + "…" if sid else "—")
        st.caption(f"Status — HTTP {resp.status_code}")
        if not detection_active:
            st.info("Detection is currently paused. Enable it in the sidebar.")
    else:
        st.warning("Could not fetch status from Pi.")
        logger.warning("GET /status failed for %s", url)


def _render_stats_tab(url: str) -> None:
    """Render detection statistics and recorded session clips.

    Fetches the full session history from the Pi's ``/detections`` endpoint
    and displays:

    - Summary metrics (session count, total time, unique classes, top class)
    - Per-class bar charts (detection count and average duration)
    - Sessions-per-day time series (when more than one day of data exists)
    - Expandable list of the 30 most recent sessions with inline MP4 playback

    Parameters
    ----------
    url:
        Pi service base URL, e.g. ``"http://raspi.local:8000"``.
    """
    st.header("Detection Statistics")

    if st.button("Refresh"):
        logger.debug("Stats refresh requested")
        st.rerun()

    resp = _get("/detections", url, timeout=5.0)
    if resp is None:
        st.error("Could not fetch detection history from Pi.")
        logger.error("GET /detections failed for %s", url)
        return

    sessions: list[dict] = resp.json()
    logger.debug("Loaded %d detection sessions", len(sessions))

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
                st.write(
                    f"**Start:** {start_dt:%H:%M:%S}"
                    + (f"  →  **End:** {end_dt:%H:%M:%S}" if end_dt else "")
                )
                st.write(f"**Duration:** {duration:.1f}s")

                obj_rows = [
                    {"Class": o["label"], "Duration (s)": round(o.get("duration_seconds", 0.0), 1)}
                    for o in session.get("objects", [])
                ]
                if obj_rows:
                    st.dataframe(pd.DataFrame(obj_rows), hide_index=True, use_container_width=True)

            with right:
                video_url = f"{url}/video/{uid}"
                st.video(video_url)


def main() -> None:
    """Entry point for the Streamlit client application.

    Configures the page, sets up logging, renders the sidebar (which returns
    the active Pi URL), then renders the Live Stream and Statistics tabs.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    st.set_page_config(
        page_title="Night Watcher",
        page_icon="🦉",
        layout="wide",
    )

    logger.info("Night Watcher client started")

    url = _render_sidebar()

    tab_stream, tab_stats = st.tabs(["📷 Live Stream", "📊 Statistics"])

    with tab_stream:
        _render_stream_tab(url)

    with tab_stats:
        _render_stats_tab(url)


if __name__ == "__main__":
    main()
