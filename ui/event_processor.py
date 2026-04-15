"""
Pipeline event processor.

process_events() is the single function that mutates
st.session_state["pipeline_steps"] in response to events from the
background graph thread. Rendering code only reads pipeline_steps;
it never writes to it.
"""

from datetime import datetime, timezone

import streamlit as st

from src.graph.store import store, put_session_meta
from ui.constants import NODE_LABELS


def process_events(events: list[dict]) -> bool:
    """
    Apply a batch of pipeline events to st.session_state["pipeline_steps"].
    Returns True when a full-page rerun is required (e.g. pipeline_done).
    Called from the _render_progress fragment on the Streamlit main thread.
    Must not call any st.* rendering functions.
    """
    steps: list[dict] = st.session_state.setdefault("pipeline_steps", [])
    idx = {s["node"]: i for i, s in enumerate(steps)}  # node → position in steps
    needs_rerun = False

    for ev in events:
        etype = ev.get("event", "")

        if etype == "node_started":
            _on_node_started(ev, steps, idx)

        elif etype == "node_completed":
            _on_node_completed(ev, steps, idx)

        elif etype == "node_failed":
            _on_node_failed(ev, steps, idx)

        elif etype == "package_inferred":
            st.session_state.inferred_package_name = ev["package_name"]
            tid = st.session_state.get("active_thread_id", "")
            if tid:
                put_session_meta(store, tid, {"package_name": ev["package_name"]})
            needs_rerun = True

        elif etype == "chapter_plan":
            chapters = ev.get("chapters", [])
            st.session_state.planned_chapters = chapters
            if "chapter_planner" in idx:
                steps[idx["chapter_planner"]]["details"] = [
                    "\n".join(f"{i+1}. {t}" for i, t in enumerate(chapters))
                ]

        elif etype == "hitl_required":
            st.session_state.hitl_pending = ev

        elif etype == "pipeline_done":
            st.session_state.pipeline_done     = True
            st.session_state.final_output_path = ev.get("output_path", "")
            st.session_state.pop("progress_q", None)
            needs_rerun = True

        elif etype == "pipeline_error":
            st.session_state.pipeline_error = ev.get("error", "Unknown error")
            st.session_state.pop("progress_q", None)
            needs_rerun = True

    return needs_rerun


# Step creation helper

def _new_step(node: str, state: str = "running", now: str = "") -> dict:
    ts = now or datetime.now(timezone.utc).isoformat()
    return {
        "node":         node,
        "label":        NODE_LABELS.get(node, node),
        "state":        state,
        "skipped":      False,
        "summary":      None,
        "details":      [],
        "error":        None,
        "started_at":   ts,
        "finished_at":  None,
        "_write_count": 0,
    }


# Individual event handlers

def _on_node_started(ev: dict, steps: list, idx: dict) -> None:
    node = ev["node"]
    if node not in idx:
        step = _new_step(node, now=ev.get("started_at", ""))
        steps.append(step)
        idx[node] = len(steps) - 1
    elif steps[idx[node]]["state"] != "running":
        steps[idx[node]]["state"]       = "running"
        steps[idx[node]]["finished_at"] = None


def _on_node_completed(ev: dict, steps: list, idx: dict) -> None:
    node    = ev["node"]
    skipped = ev.get("skipped", False)
    now     = datetime.now(timezone.utc).isoformat()

    # write_review_chapter is a fan-out — many parallel nodes, one aggregated step.
    if node == "write_review_chapter":
        _on_write_review_chapter(ev, steps, idx, now)
        return

    # All other nodes: create or update their step entry.
    if node in idx:
        step = steps[idx[node]]
    else:
        step = _new_step(node, now=now)
        steps.append(step)
        idx[node] = len(steps) - 1

    step["state"]       = "skipped" if skipped else "complete"
    step["skipped"]     = skipped
    step["summary"]     = ev.get("summary", "")
    step["details"]     = ev.get("details", [])
    step["finished_at"] = now

    # chapter_crossref starting implies write_review_chapter is fully done.
    if node == "chapter_crossref" and "write_review_chapter" in idx:
        wr = steps[idx["write_review_chapter"]]
        if wr["state"] == "running":
            wr["state"]       = "complete"
            wr["finished_at"] = now


def _on_write_review_chapter(ev: dict, steps: list, idx: dict, now: str) -> None:
    """Accumulate parallel chapter-write results into a single aggregated step."""
    node  = "write_review_chapter"
    total = ev.get("total_chapters", 0)

    if node not in idx:
        step = _new_step(node, now=now)
        steps.append(step)
        idx[node] = len(steps) - 1

    step = steps[idx[node]]
    step["_write_count"] = step.get("_write_count", 0) + 1
    count = step["_write_count"]

    chapter_result = ev.get("_chapter_result", {})
    if chapter_result:
        title    = chapter_result.get("title", f"Chapter {count}")
        accepted = chapter_result.get("accepted", False)
        iters    = chapter_result.get("iterations", 1)
        mark     = "✓" if accepted else "✗"
        step["details"].append(f"{count}. **{title}** — {mark} in {iters} iteration(s)")

    step["summary"] = (
        f"Writing chapters: {count}/{total} complete"
        if total else f"{count} chapter(s) written"
    )
    if total and count >= total:
        step["state"]       = "complete"
        step["finished_at"] = now


def _on_node_failed(ev: dict, steps: list, idx: dict) -> None:
    node = ev["node"]
    now  = datetime.now(timezone.utc).isoformat()
    if node in idx:
        step = steps[idx[node]]
        step["state"]       = "error"
        step["error"]       = ev.get("error", "Unknown error")
        step["finished_at"] = now
