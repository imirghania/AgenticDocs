"""
Resumption inspector — the first node in the DocSmith graph.

On every graph invocation this node:
1. Reads sessions/{thread_id}/ to find which nodes already completed.
2. For each completed node, reloads its output back into state.
3. Sets completed_nodes, scratchpad_dir, is_resuming, and resumption_summary.
4. Emits a user-visible message summarising what will be skipped vs run.
5. Creates or updates the long-term session metadata entry.

This node is NEVER skipped — it runs unconditionally on every invocation.
"""
import json
from pathlib import Path
from datetime import datetime, timezone

from src.graph.scratchpad import (
    list_completed_nodes,
    read_scratchpad,
    SCRATCHPAD_FILES,
)
from src.graph.store import put_session_meta, store as global_store
from src.state import DocSmithState

# Import DimensionScore so we can deserialise quality_report correctly.
# enrichment_node accesses .gaps on each entry; plain dicts would fail.
from src.components.quality_judge import DimensionScore

# Ordered list of all pipeline nodes (for the "will run" summary)
_ALL_NODES = [
    "web_discovery",
    "confirm_package",
    "context7_agent",
    "docs_scraper",
    "github_agent",
    "quality_judge",
    "enrichment_agent",
    "chapter_planner",
    "chapter_crossref",
    "writer_agent",
]


async def resumption_inspector_node(state: DocSmithState) -> dict:
    thread_id = state["thread_id"]
    user_id = state.get("user_id", "anonymous")
    scratchpad_dir = f"sessions/{thread_id}"
    Path(scratchpad_dir).mkdir(parents=True, exist_ok=True)

    completed = list_completed_nodes(thread_id)
    is_resuming = bool(completed)

    updates: dict = {
        "scratchpad_dir": scratchpad_dir,
        "completed_nodes": completed,
        "is_resuming": is_resuming,
        "thread_id": thread_id,
        "user_id": user_id,
    }

    # Restore state from each completed node's scratchpad file
    scratchpad_file_paths: list[str] = []

    if "web_discovery" in completed:
        raw = read_scratchpad(thread_id, "web_discovery")
        if raw:
            updates["search_results"] = json.loads(raw)

    if "confirm_package" in completed:
        raw = read_scratchpad(thread_id, "confirm_package")
        if raw:
            data = json.loads(raw)
            updates["confirmed_package"] = data.get("confirmed_package")
            updates["github_url"] = data.get("github_url")
            updates["docs_url"] = data.get("docs_url")
            # Restore package_name override if user chose "none"
            if data.get("package_name"):
                updates["package_name"] = data["package_name"]

    # Markdown scratchpad files: restore their paths so quality_judge can read them
    for key in ("context7_agent", "docs_scraper", "github_agent", "enrichment_agent"):
        if key in completed:
            path = Path("sessions") / thread_id / SCRATCHPAD_FILES[key]
            if path.exists() and path.read_text(encoding="utf-8").strip():
                scratchpad_file_paths.append(str(path))

    if scratchpad_file_paths:
        updates["scratchpad_files"] = scratchpad_file_paths

    if "quality_judge" in completed:
        raw = read_scratchpad(thread_id, "quality_judge")
        if raw:
            data = json.loads(raw)
            updates["quality_score"] = data.get("quality_score")
            raw_report: dict = data.get("quality_report", {})
            # Deserialise plain dicts back to DimensionScore objects so that
            # enrichment_node can call .gaps without AttributeError.
            updates["quality_report"] = {
                k: DimensionScore(**v) if isinstance(v, dict) else v
                for k, v in raw_report.items()
            }

    if "chapter_planner" in completed:
        raw = read_scratchpad(thread_id, "chapter_planner")
        if raw:
            data = json.loads(raw)
            updates["chapters"] = data.get("chapters")
            updates["chapter_plan"] = data.get("chapter_plan", [])

    # chapter_crossref: no state field to restore — enriched files are already
    # on disk in output/{package_slug}/. The completed_nodes set prevents re-run.
    if "writer_agent" in completed:
        raw = read_scratchpad(thread_id, "writer_agent")
        if raw:
            updates["final_documentation"] = raw


    # Build user-visible summary
    if is_resuming:
        pending = [n for n in _ALL_NODES if n not in completed]
        summary = (
            f"Resuming session. Already completed: {', '.join(sorted(completed))}. "
            + (f"Will run: {' → '.join(pending)}." if pending else "All nodes complete.")
        )
    else:
        summary = "Starting fresh documentation generation."

    updates["resumption_summary"] = summary
    updates["messages"] = [("assistant", summary)]


    # Update long-term session store
    now = datetime.now(timezone.utc).isoformat()
    put_session_meta(global_store, thread_id, {
        "thread_id": thread_id,
        "user_id": user_id,
        "status": "running",
        "created_at": now,   # put_session_meta merges; only written on first call
        "last_completed_node": max(completed, key=_ALL_NODES.index, default=None)
        if completed else None,
    })

    return updates
