"""Streamlit client for Night Watcher — runs on your local machine.

Connects to the FastAPI detection service running on the Raspberry Pi and
displays a live MJPEG stream alongside detection statistics, health
monitoring, and recorded video clips.  Configure the Pi URL via the
RASPI_URL environment variable or the sidebar input.

Usage
-----
    RASPI_URL=http://raspi.local:8000 streamlit run app.py
"""

import logging
import os
from collections import Counter
from datetime import datetime, time as dt_time
from typing import Any

import pandas as pd
import requests
import streamlit as st

DEFAULT_URL = os.getenv("RASPI_URL", "http://raspberrypi.local:8000")

logger = logging.getLogger("night_watcher.client")


def _post(path: str, base_url: str, payload: dict[str, Any], timeout: float = 3.0) -> requests.Response | None:
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


def _get(path: str, base_url: str, timeout: float = 3.0) -> requests.Response | None:
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
        st.image("assets/logo.jpeg", width="stretch")
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
            cfg: dict[str, Any] = cfg_resp.json()
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
                    cfg_back: dict[str, Any] = result.json()
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
    """Render the live MJPEG stream with frame timestamp and full-screen controls.

    The stream is embedded via an HTML ``<img>`` tag pointing directly at the
    Pi's MJPEG endpoint.  A timestamp overlay is burned into every frame
    server-side so viewers can confirm the feed is live.  A full-screen
    button opens the raw MJPEG URL in a new browser tab.

    Parameters
    ----------
    url:
        Pi service base URL, e.g. ``"http://raspi.local:8000"``.
    """
    stream_url = f"{url}/stream"
    logger.debug("Rendering stream from %s", stream_url)

    # Header row: title + full-screen link
    col_title, col_fs = st.columns([6, 1])
    with col_title:
        st.subheader("Live Camera Feed")
    with col_fs:
        st.markdown(
            f'<a href="{stream_url}" target="_blank">'
            '<button style="margin-top:8px;padding:6px 14px;border-radius:6px;'
            "border:1px solid #555;background:#1e1e2e;color:#cdd6f4;"
            'cursor:pointer;font-size:13px;">⛶ Full Screen</button></a>',
            unsafe_allow_html=True,
        )

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
            background: #000;
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
            id="stream-img"
            src="{stream_url}"
            onerror="this.alt='Stream unavailable — is the Pi running?'"
          />
        </div>
        """,
        height=500,
        scrolling=False,
    )

    resp = _get("/status", url)
    if resp is not None:
        status: dict[str, Any] = resp.json()
        classes: str = ", ".join(status.get("detected_classes", [])) or "none"
        sid: str = status.get("session_id") or ""
        detection_active: bool = status.get("detection_active", True)
        frame_age_ms: int | None = status.get("frame_age_ms")
        captured_at: float | None = status.get("frame_captured_at")

        logger.debug(
            "Status: detection_active=%s classes=%s session=%s",
            detection_active, classes, sid,
        )

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Detection", "Active" if detection_active else "Paused")
        col2.metric("Detected", classes)
        col3.metric("Session", sid[:8] + "…" if sid else "—")

        if captured_at and frame_age_ms is not None:
            frame_ts = datetime.fromtimestamp(captured_at).strftime("%H:%M:%S")
            col4.metric("Last Frame", frame_ts, delta=f"{frame_age_ms} ms ago", delta_color="off")
        else:
            col4.metric("Last Frame", "—")

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

    sessions: list[dict[str, Any]] = resp.json()
    logger.debug("Loaded %d detection sessions", len(sessions))

    if not sessions:
        st.info("No detection sessions recorded yet.")
        return

    all_objects: list[dict[str, Any]] = [o for s in sessions for o in s.get("objects", [])]
    class_counter: Counter[str] = Counter(o["label"] for o in all_objects)
    total_duration: float = sum(s.get("duration_seconds", 0.0) for s in sessions)
    top_class: str = class_counter.most_common(1)[0][0] if class_counter else "—"

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
        start_dt: datetime = datetime.fromtimestamp(session["start_time"])
        duration: float = session.get("duration_seconds", 0.0)
        classes: str = ", ".join(sorted({o["label"] for o in session.get("objects", [])}))

        with st.expander(f"{start_dt:%Y-%m-%d %H:%M:%S}  —  {classes}  ({duration:.1f}s)"):
            left, right = st.columns([1, 2])

            with left:
                st.write(f"**UUID:** `{uid}`")
                end_ts: float | None = session.get("end_time")
                end_dt: datetime | None = datetime.fromtimestamp(end_ts) if end_ts else None
                st.write(
                    f"**Start:** {start_dt:%H:%M:%S}"
                    + (f"  →  **End:** {end_dt:%H:%M:%S}" if end_dt else "")
                )
                st.write(f"**Duration:** {duration:.1f}s")

                obj_rows: list[dict[str, Any]] = [
                    {"Class": o["label"], "Duration (s)": round(o.get("duration_seconds", 0.0), 1)}
                    for o in session.get("objects", [])
                ]
                if obj_rows:
                    st.dataframe(pd.DataFrame(obj_rows), hide_index=True, width="stretch")

            with right:
                video_url = f"{url}/video/{uid}"
                st.video(video_url)


def _render_health_tab(url: str) -> None:
    """Render system health, Docker service status, app metrics, and logs.

    Polls the Pi's ``/health/detailed``, ``/health/docker``,
    ``/metrics/app``, and ``/logs`` endpoints.  Uses ``st.fragment`` with
    ``run_every`` for lightweight auto-refresh without a full page reload.

    Parameters
    ----------
    url:
        Pi service base URL, e.g. ``"http://raspi.local:8000"``.
    """

    @st.fragment(run_every=10)
    def _power_status() -> None:
        resp = _get("/health/power", url, timeout=4.0)
        st.subheader("Power & Throttle Status")
        st.caption(f"Refreshes every 10 s — last update {datetime.now().strftime('%H:%M:%S')}")

        if resp is None:
            st.warning("Could not fetch power status from Pi.")
            return

        p: dict[str, Any] = resp.json()

        if p.get("error"):
            st.warning(f"vcgencmd unavailable: {p['error']}")
            return

        healthy = p.get("healthy")
        if healthy is True:
            st.success("Power OK — no throttling or under-voltage detected since last boot")
        elif healthy is False:
            st.error("Power issue detected — check your USB-C power supply (minimum 5V / 3A)")

        col1, col2, col3, col4 = st.columns(4)
        col1.metric(
            "Under-voltage now",
            "⚡ YES" if p.get("under_voltage_now") else "✅ No",
            help="Voltage currently below 4.63 V",
        )
        col2.metric(
            "Throttled now",
            "🔴 YES" if p.get("throttled_now") else "✅ No",
            help="CPU currently throttled due to power or heat",
        )
        col3.metric(
            "Under-voltage (ever)",
            "⚡ YES" if p.get("under_voltage_occurred") else "✅ No",
            help="Under-voltage occurred at any point since last boot",
        )
        col4.metric(
            "Throttled (ever)",
            "🔴 YES" if p.get("throttling_occurred") else "✅ No",
            help="CPU throttling occurred since last boot",
        )

        with st.expander("All throttle flags"):
            flags = {
                "Under-voltage now": p.get("under_voltage_now"),
                "Freq capped now": p.get("freq_capped_now"),
                "Throttled now": p.get("throttled_now"),
                "Soft temp limit now": p.get("soft_temp_limit_now"),
                "Under-voltage occurred": p.get("under_voltage_occurred"),
                "Freq capping occurred": p.get("freq_capping_occurred"),
                "Throttling occurred": p.get("throttling_occurred"),
                "Soft temp limit occurred": p.get("soft_temp_limit_occurred"),
            }
            rows = [
                {"Flag": k, "State": "⚠️ YES" if v else "✅ No"}
                for k, v in flags.items()
            ]
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
            st.caption(f"Raw throttled value: `{p.get('throttled_raw', '?')}`")

    @st.fragment(run_every=5)
    def _system_metrics() -> None:
        resp = _get("/health/detailed", url, timeout=4.0)
        if resp is None:
            st.warning("Could not fetch system health from Pi.")
            return

        h: dict[str, Any] = resp.json()
        st.subheader("System Health")
        st.caption(f"Refreshes every 5 s — last update {datetime.now().strftime('%H:%M:%S')}")

        cpu = h.get("cpu", {})
        mem = h.get("memory", {})
        disk = h.get("disk", {})
        temp = h.get("temperature_c")
        uptime_s = h.get("uptime_seconds", 0)

        # Uptime formatted
        uptime_str = _fmt_uptime(uptime_s)

        # Top-level KPIs
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("CPU", f"{cpu.get('percent', '?')}%",
                  help=f"{cpu.get('count')} cores @ {cpu.get('frequency_mhz')} MHz")
        k2.metric("Memory", f"{mem.get('percent', '?')}%",
                  help=f"{mem.get('used_mb')} / {mem.get('total_mb')} MB used")
        k3.metric("Disk", f"{disk.get('percent', '?')}%",
                  help=f"{disk.get('used_gb')} / {disk.get('total_gb')} GB used")
        if temp is not None:
            color = "🟢" if temp < 60 else ("🟡" if temp < 75 else "🔴")
            k4.metric("Temperature", f"{color} {temp} °C")
        else:
            k4.metric("Temperature", "N/A")

        # Progress bars
        col_a, col_b = st.columns(2)
        with col_a:
            st.write("**CPU Usage**")
            st.progress(cpu.get("percent", 0) / 100,
                        text=f"{cpu.get('percent')}% — {cpu.get('frequency_mhz')} MHz")
            st.write("**Memory**")
            st.progress(mem.get("percent", 0) / 100,
                        text=f"{mem.get('used_mb')} MB / {mem.get('total_mb')} MB")
        with col_b:
            st.write("**Disk (assets)**")
            st.progress(disk.get("percent", 0) / 100,
                        text=f"{disk.get('used_gb')} GB / {disk.get('total_gb')} GB free: {disk.get('free_gb')} GB")
            swap = h.get("swap", {})
            if swap.get("total_mb", 0) > 0:
                st.write("**Swap**")
                st.progress(swap.get("percent", 0) / 100,
                            text=f"{swap.get('used_mb')} MB / {swap.get('total_mb')} MB")

        st.caption(f"Uptime: {uptime_str}")

    @st.fragment(run_every=10)
    def _docker_services() -> None:
        st.subheader("Docker Services")
        resp = _get("/health/docker", url, timeout=5.0)
        if resp is None:
            st.warning("Could not fetch Docker service list from Pi.")
            return

        services: list[dict[str, str]] = resp.json()
        if not services:
            st.info("No containers found.")
            return

        rows = []
        for svc in services:
            state = svc.get("state", "")
            icon = "🟢" if state == "running" else ("🔴" if state in ("exited", "dead") else "🟡")
            rows.append(
                {
                    "": icon,
                    "Name": svc.get("name", ""),
                    "Image": svc.get("image", ""),
                    "Status": svc.get("status", ""),
                    "Ports": svc.get("ports", ""),
                    "Created": svc.get("created", ""),
                }
            )
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    @st.fragment(run_every=5)
    def _app_metrics() -> None:
        st.subheader("Application Metrics")
        resp = _get("/metrics/app", url, timeout=3.0)
        if resp is None:
            st.warning("Could not fetch application metrics from Pi.")
            return

        m: dict[str, Any] = resp.json()
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Frames Processed", f"{m.get('frames_total', 0):,}")
        c2.metric("Avg FPS", f"{m.get('fps_avg', 0):.1f}")
        c3.metric("Avg Frame Time", f"{m.get('avg_processing_ms', 0):.1f} ms")
        c4.metric("Sessions", m.get("sessions_total", 0))

        dbc: dict[str, int] = m.get("detections_by_class", {})
        if dbc:
            st.write("**Detections by class (since startup)**")
            df = pd.DataFrame(
                sorted(dbc.items(), key=lambda x: -x[1]),
                columns=["Class", "Count"],
            ).set_index("Class")
            st.bar_chart(df)

    @st.fragment(run_every=10)
    def _log_viewer() -> None:
        st.subheader("Application Logs")
        col_lvl, col_lim, col_ref = st.columns([2, 2, 1])
        with col_lvl:
            level_filter = st.selectbox(
                "Level", ["ALL", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
                index=2, key="log_level_filter",
            )
        with col_lim:
            log_limit = st.number_input(
                "Max entries", min_value=10, max_value=500, value=100, step=10,
                key="log_limit",
            )
        with col_ref:
            st.write("")
            st.write("")
            if st.button("Refresh logs"):
                st.rerun(scope="fragment")

        lvl_param = None if level_filter == "ALL" else level_filter
        resp = _get(
            f"/logs?limit={int(log_limit)}"
            + (f"&level={lvl_param}" if lvl_param else ""),
            url,
            timeout=5.0,
        )
        if resp is None:
            st.warning("Could not fetch logs from Pi.")
            return

        log_entries: list[dict[str, Any]] = resp.json()
        if not log_entries:
            st.info("No log entries found.")
            return

        rows = []
        for entry in log_entries:
            ts = datetime.fromtimestamp(entry["timestamp"]).strftime("%H:%M:%S")
            level = entry.get("level", "")
            icon = {"DEBUG": "⚪", "INFO": "🔵", "WARNING": "🟡", "ERROR": "🔴", "CRITICAL": "🟣"}.get(level, "⚫")
            rows.append(
                {
                    "Time": ts,
                    "Level": f"{icon} {level}",
                    "Logger": entry.get("logger", ""),
                    "Message": entry.get("message", ""),
                }
            )
        st.dataframe(
            pd.DataFrame(rows),
            hide_index=True,
            width="stretch",
            height=min(400, 35 + len(rows) * 35),
        )

    _power_status()
    st.divider()
    _system_metrics()
    st.divider()
    _docker_services()
    st.divider()
    _app_metrics()
    st.divider()
    _log_viewer()


def _fmt_uptime(seconds: int) -> str:
    """Format uptime seconds as a human-readable string."""
    d, rem = divmod(int(seconds), 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if d:
        parts.append(f"{d}d")
    if h:
        parts.append(f"{h}h")
    if m:
        parts.append(f"{m}m")
    parts.append(f"{s}s")
    return " ".join(parts)


def main() -> None:
    """Entry point for the Streamlit client application.

    Configures the page, sets up logging, renders the sidebar (which returns
    the active Pi URL), then renders the Live Stream, Statistics, and Health
    tabs.
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

    tab_stream, tab_stats, tab_health = st.tabs(
        ["📷 Live Stream", "📊 Statistics", "🩺 Health"]
    )

    with tab_stream:
        _render_stream_tab(url)

    with tab_stats:
        _render_stats_tab(url)

    with tab_health:
        _render_health_tab(url)


if __name__ == "__main__":
    main()
