"""
Session utilities: user identity persistence, scratchpad restore, time helpers.
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

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


def relative_time(iso_str: str) -> str:
    """Return a human-readable relative time string (e.g. '5m ago')."""
    try:
        dt   = datetime.fromisoformat(iso_str)
        secs = int((datetime.now(timezone.utc) - dt).total_seconds())
        if secs < 60:    return f"{secs}s ago"
        if secs < 3600:  return f"{secs // 60}m ago"
        if secs < 86400: return f"{secs // 3600}h ago"
        return f"{secs // 86400}d ago"
    except Exception:
        return iso_str[:10] if iso_str else ""


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
