"""
Session utilities: user identity persistence, scratchpad restore, time helpers.
"""

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import streamlit as st

from src.graph.scratchpad import list_completed_nodes, read_scratchpad, SCRATCHPAD_FILES
from ui.constants import NODE_LABELS, NODE_ORDER
from ui.step_formatter import format_step_output


_USER_ID_FILE = Path("sessions") / ".user_id"


def get_or_create_user_id() -> str:
    """
    Return a persistent user_id that survives Streamlit process restarts.
    On first run a new UUID is generated and saved to sessions/.user_id.
    """
    _USER_ID_FILE.parent.mkdir(parents=True, exist_ok=True)
    if _USER_ID_FILE.exists():
        uid = _USER_ID_FILE.read_text(encoding="utf-8").strip()
        if uid:
            return uid
    uid = str(uuid.uuid4())
    _USER_ID_FILE.write_text(uid, encoding="utf-8")
    return uid


def _parse_utc(iso_str: str) -> datetime:
    """Parse an ISO-8601 string (with or without Z suffix) to a UTC-aware datetime."""
    normalised = re.sub(r"Z$", "+00:00", iso_str.strip())
    dt = datetime.fromisoformat(normalised)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def get_local_tz() -> timezone | ZoneInfo:
    """
    Determine the local timezone once per session, cached in st.session_state.

    Priority:
      1. TZ environment variable → ZoneInfo(TZ)
      2. Fallback → UTC (a one-time sidebar caption is shown by render_sidebar)
    """
    cached = st.session_state.get("resolved_tz")
    if cached is not None:
        return cached
    tz_env = os.environ.get("TZ", "").strip()
    if tz_env:
        try:
            tz = ZoneInfo(tz_env)
            st.session_state["resolved_tz"] = tz
            return tz
        except (ZoneInfoNotFoundError, Exception):
            pass
    st.session_state["resolved_tz"] = timezone.utc
    return timezone.utc


def format_local_time(iso_str: str) -> str:
    """
    Convert a UTC ISO-8601 string to a human-readable local time string.

    Uses get_local_tz() for the local offset. Returns relative strings for
    recent events and an absolute local date for events older than one week.
    """
    if not iso_str:
        return "unknown"
    try:
        utc_dt   = _parse_utc(iso_str)
        local_tz = get_local_tz()
        local_dt = utc_dt.astimezone(local_tz)
        now_local = datetime.now(local_tz)
        delta   = now_local - local_dt
        seconds = int(delta.total_seconds())
        if seconds < 0:
            return "just now"
        if seconds < 60:
            return "just now"
        if seconds < 3600:
            m = seconds // 60
            return f"{m} min ago"
        if seconds < 86400:
            h = seconds // 3600
            return f"{h} h ago"
        if seconds < 7 * 86400:
            d = seconds // 86400
            return f"{d} d ago"
        return local_dt.strftime("%-d %b %Y, %H:%M")
    except Exception:
        return iso_str[:16]


def relative_time(iso_str: str) -> str:
    """Legacy wrapper — delegates to format_local_time."""
    return format_local_time(iso_str)


def restore_steps_from_scratchpad(thread_id: str) -> list[dict]:
    """
    Rebuild a pipeline_steps list from scratchpad files on disk.
    Called when resuming a paused session or viewing a completed one.
    Only nodes whose scratchpad file exists and is non-empty are included.
    """
    completed = list_completed_nodes(thread_id)
    now       = datetime.now(timezone.utc).isoformat()
    steps: list[dict] = []

    for node_name in NODE_ORDER:
        if node_name not in SCRATCHPAD_FILES or node_name not in completed:
            continue

        node_data: dict = {}
        raw = read_scratchpad(thread_id, node_name)

        # Deserialise JSON payloads so format_step_output can render them.
        if node_name in ("web_discovery",) and raw:
            try:
                node_data["search_results"] = json.loads(raw)
            except Exception:
                pass
        elif node_name == "confirm_package" and raw:
            try:
                node_data = json.loads(raw)
            except Exception:
                pass
        elif node_name == "quality_judge" and raw:
            try:
                d = json.loads(raw)
                node_data["quality_score"]  = d.get("quality_score", 0)
                node_data["quality_report"] = d.get("quality_report", {})
            except Exception:
                pass
        elif node_name == "chapter_planner" and raw:
            try:
                node_data["chapter_plan"] = json.loads(raw).get("chapter_plan", [])
            except Exception:
                pass
        elif node_name == "writer_agent" and raw:
            node_data["final_documentation"] = raw

        step_info = format_step_output(node_name, node_data, thread_id)
        steps.append({
            "node":         node_name,
            "label":        NODE_LABELS.get(node_name, node_name),
            "state":        "skipped",
            "skipped":      True,
            "summary":      step_info.get("summary", ""),
            "details":      step_info.get("details", []),
            "error":        None,
            "started_at":   now,
            "finished_at":  now,
            "_write_count": 0,
        })

    return steps
