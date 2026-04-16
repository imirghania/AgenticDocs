"""
Background graph execution.

start_graph_thread() is called from the Streamlit main thread.
It launches a daemon thread that runs its own asyncio event loop,
streams LangGraph updates, and communicates back via two queues:

    progress_q  (graph → Streamlit)  pipeline events
    hitl_q      (Streamlit → graph)  human-in-the-loop responses
"""

import asyncio
import queue
import threading
from datetime import datetime, timezone
from typing import Any

import streamlit as st
from langgraph.types import Command

from src.graph.store import store, put_session_meta
from ui.step_formatter import format_step_output


def start_graph_thread(thread_id: str, user_id: str, package_request: str) -> None:
    """
    Initialise Streamlit session state, record the session in the store,
    and launch the daemon graph thread.
    Called from the Streamlit main thread.
    """
    progress_q = queue.Queue()
    hitl_q     = queue.Queue()

    st.session_state.progress_q            = progress_q
    st.session_state.hitl_q                = hitl_q
    st.session_state.hitl_pending          = None
    st.session_state.pipeline_done         = False
    st.session_state.pipeline_error        = None
    st.session_state.final_output_path     = None
    st.session_state.planned_chapters      = []
    st.session_state.inferred_package_name = None
    st.session_state.pipeline_steps        = []

    now = datetime.now(timezone.utc).isoformat()
    put_session_meta(store, thread_id, {
        "thread_id":    thread_id,
        "user_id":      user_id,
        "package_name": package_request,
        "language":     "",
        "created_at":   now,
        "updated_at":   now,
        "status":       "running",
    })

    t = threading.Thread(
        target=_run_graph_thread,
        args=(thread_id, user_id, package_request, progress_q, hitl_q,
            st.session_state.graph),
        daemon=True,
    )
    st.session_state.graph_thread = t
    t.start()


# Daemon thread entry point 

def _run_graph_thread(
    thread_id: str,
    user_id: str,
    package_request: str,
    progress_q: queue.Queue,
    hitl_q: queue.Queue,
    graph: Any,
) -> None:
    """Create a dedicated asyncio event loop and run the graph inside it."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(
            _run_graph_async(thread_id, user_id, package_request, progress_q, hitl_q, graph)
        )
    except Exception as exc:
        progress_q.put({"event": "pipeline_error", "error": str(exc)})
    finally:
        loop.close()


# Async graph runner

async def _run_graph_async(
    thread_id: str,
    user_id: str,
    package_request: str,
    progress_q: queue.Queue,
    hitl_q: queue.Queue,
    graph: Any,
) -> None:
    """
    Stream graph events until completion or interruption.

    stream_mode=["updates", "debug"]:
      "debug" task events  → fire node_started before the node runs
      "updates" events     → fire node_completed after the node finishes
    """
    config = {"configurable": {"thread_id": thread_id, "user_id": user_id}}
    initial_state: dict = {
        "messages":        [("user", package_request)],
        "thread_id":       thread_id,
        "user_id":         user_id,
        "completed_nodes": set(),
        "scratchpad_files": [],
        "chapter_results":  [],
        "chapter_plan":     [],
    }
    current_input: Any = initial_state
    total_chapters = 0  # updated when chapter_planner completes

    while True:
        interrupt_hit = False
        astream = graph.astream(current_input, config=config, stream_mode=["updates", "debug"])
        try:
            async for mode, data in astream:
                if mode == "debug":
                    _handle_debug_event(data, progress_q)
                    continue

                if mode != "updates":
                    continue

                if "__interrupt__" in data:
                    current_input, interrupt_hit = await _handle_interrupt(
                        data, thread_id, progress_q, hitl_q
                    )
                    break

                for node_name, node_data in data.items():
                    if node_name.startswith("__"):
                        continue
                    nd      = node_data if isinstance(node_data, dict) else {}
                    skipped = nd == {}

                    if node_name == "chapter_planner" and not skipped:
                        total_chapters = len(nd.get("chapter_plan", []))

                    _emit_node_completed(node_name, nd, skipped, total_chapters, thread_id, progress_q)
                    _emit_side_channel_events(node_name, nd, thread_id, progress_q)
        finally:
            await astream.aclose()

        if not interrupt_hit:
            break

    put_session_meta(store, thread_id, {"status": "completed"})
    # Sentinel in case chapter_assembler didn't emit output_file
    progress_q.put({"event": "pipeline_done", "output_path": "", "thread_id": thread_id})


# Event helpers 

def _handle_debug_event(data: dict, progress_q: queue.Queue) -> None:
    """Extract node_started signal from LangGraph debug task events (best-effort)."""
    try:
        if data.get("type") == "task":
            node_name = data.get("payload", {}).get("name", "")
            if node_name and not node_name.startswith("__"):
                progress_q.put({
                    "event":      "node_started",
                    "node":       node_name,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                })
    except Exception:
        pass


async def _handle_interrupt(
    data: dict,
    thread_id: str,
    progress_q: queue.Queue,
    hitl_q: queue.Queue,
) -> tuple[Any, bool]:
    """Pause the pipeline for HITL input and return (resumed_input, True)."""
    payload = data["__interrupt__"][0].value
    progress_q.put({"event": "hitl_required", **payload})
    put_session_meta(store, thread_id, {"status": "paused"})

    user_resp: Any = await asyncio.get_event_loop().run_in_executor(
        None, lambda: hitl_q.get(timeout=300)
    )
    put_session_meta(store, thread_id, {"status": "running"})
    # Structured dict responses (new HITL types like existing_doc_found,
    # partial_cache_found, update_assessment) pass through directly.
    # Plain strings (package_confirmation / package_clarification) are
    # wrapped as {"text": ...} to match confirm_package_node's expectation.
    if isinstance(user_resp, dict):
        return Command(resume=user_resp), True
    return Command(resume={"text": user_resp}), True


def _emit_node_completed(
    node_name: str,
    nd: dict,
    skipped: bool,
    total_chapters: int,
    thread_id: str,
    progress_q: queue.Queue,
) -> None:
    step_info = format_step_output(node_name, nd, thread_id)
    progress_q.put({
        "event":          "node_completed",
        "node":           node_name,
        "skipped":        skipped,
        "summary":        step_info.get("summary", ""),
        "details":        step_info.get("details", []),
        "total_chapters": total_chapters,
        # pass through extras like _chapter_plan, _chapter_result
        **{k: v for k, v in step_info.items() if k not in ("summary", "details")},
    })


def _emit_side_channel_events(
    node_name: str,
    nd: dict,
    thread_id: str,
    progress_q: queue.Queue,
) -> None:
    """Push secondary events consumed by the Streamlit UI (not pipeline_steps)."""
    if node_name == "intent_parser" and nd.get("package_name"):
        progress_q.put({
            "event":        "package_inferred",
            "package_name": nd["package_name"],
            "language":     nd.get("language", ""),
            "ecosystem":    nd.get("ecosystem", ""),
        })

    if node_name == "chapter_planner" and nd.get("chapter_plan"):
        progress_q.put({"event": "chapter_plan", "chapters": nd["chapter_plan"]})

    if node_name == "chapter_assembler" and nd.get("output_file"):
        progress_q.put({
            "event":       "pipeline_done",
            "output_path": nd["output_file"],
            "thread_id":   thread_id,
        })

    # Cache inspector: "view" decision — signal the UI to show the existing doc
    if node_name == "local_cache_inspector" and nd.get("cache_decision") == "view":
        progress_q.put({
            "event":     "view_existing_doc",
            "thread_id": nd.get("cache_source_thread_id", ""),
        })
