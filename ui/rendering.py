"""
All Streamlit rendering functions.

Sections:
  1. Detail renderer      — render_detail()
  2. Pipeline step list   — render_pipeline_steps()
  3. HITL widget          — render_hitl()
  4. Final output         — render_final_output()
  5. Progress fragment    — render_progress()   ← @st.fragment, polls every second
  6. Session views        — render_new_session_form / render_active_session / render_completed_session
  7. Sidebar              — render_sidebar()
"""

import queue
import uuid
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

from src.graph.scratchpad import read_scratchpad, SCRATCHPAD_FILES
from src.graph.store import store, get_session_meta, list_user_sessions, delete_session
from ui.constants import NODE_LABELS, STATUS_COLORS, NONE_OF_THE_ABOVE
from ui.event_processor import process_events
from ui.graph_runner import start_graph_thread
from ui.session import relative_time, restore_steps_from_scratchpad


# ── 1. Detail renderer ────────────────────────────────────────────────────────

def render_detail(detail: str) -> None:
    """
    Render one detail string choosing the right Streamlit widget:
      - JSON object/array  → st.code (json)
      - Markdown table     → st.markdown (allows HTML colour spans)
      - Indented block     → st.code (plain)
      - Everything else    → st.markdown
    """
    if not detail.strip():
        return
    lines      = detail.strip().split("\n")
    first_line = lines[0].strip()
    is_multi   = len(lines) > 1

    if first_line.startswith(("{", "[")):
        st.code(detail, language="json")
    elif is_multi and first_line.startswith("|") and "---" in detail:
        st.markdown(detail, unsafe_allow_html=True)
    elif is_multi and (
        first_line.startswith("#")
        or any(ln.startswith(("  ", "\t")) for ln in lines[1:5])
    ):
        st.code(detail, language="")
    else:
        st.markdown(detail, unsafe_allow_html=True)


# ── 2. Pipeline step list ─────────────────────────────────────────────────────

def render_pipeline_steps() -> None:
    """
    Render each entry in st.session_state["pipeline_steps"] as an st.status block.
    Pure read — never mutates session_state.
    """
    for step in st.session_state.get("pipeline_steps", []):
        state     = step["state"]
        node_name = step["node"]

        if state == "running":
            st_state, expanded, label = "running", True, step["label"]
        elif state == "skipped":
            st_state, expanded, label = "complete", False, f"Cached — {step['label']}"
        elif state == "error":
            st_state, expanded, label = "error", True, step["label"]
        else:  # complete
            st_state, expanded, label = "complete", False, step["label"]

        with st.status(label, state=st_state, expanded=expanded):
            _render_step_body(step, node_name, state)
            _render_step_footer(step)


def _render_step_body(step: dict, node_name: str, state: str) -> None:
    summary = step.get("summary")
    if state == "running":
        st.markdown("Working…")
    elif state == "skipped":
        filename = SCRATCHPAD_FILES.get(node_name, "")
        st.markdown(
            f"Loaded from scratchpad: `{filename}`"
            + (f"\n\n{summary}" if summary else "")
        )
    elif state == "error":
        st.markdown(f"**Failed:** {step.get('error', 'Unknown error')}")
    elif summary:
        st.markdown(summary, unsafe_allow_html=True)

    for detail in step.get("details", []):
        render_detail(detail)


def _render_step_footer(step: dict) -> None:
    started  = step.get("started_at", "")
    finished = step.get("finished_at")
    if not started:
        return
    try:
        d1 = datetime.fromisoformat(started)
        if finished:
            d2       = datetime.fromisoformat(finished)
            duration = f"{(d2 - d1).total_seconds():.1f}s"
            footer   = f"Started {started[11:19]}  ·  Duration {duration}"
        else:
            footer = f"Started {started[11:19]}"
        _, col = st.columns([8, 2])
        with col:
            st.caption(footer)
    except Exception:
        pass


# ── 3. HITL widget ────────────────────────────────────────────────────────────

def render_hitl(payload: dict) -> None:
    """Render the human-in-the-loop package confirmation or clarification widget."""
    hitl_q = st.session_state.hitl_q
    ptype  = payload.get("type")

    if ptype == "package_confirmation":
        results = payload.get("results", [])
        options = [f"{r.get('title', '')} — {r.get('url', '')}" for r in results]
        options.append(NONE_OF_THE_ABOVE)

        st.markdown("### Which package did you mean?")
        choice        = st.radio(payload.get("message", "Select a result:"), options, key="hitl_radio")
        none_selected = choice == NONE_OF_THE_ABOVE
        clarification = ""

        if none_selected:
            clarification = st.text_area(
                "Describe the package you're looking for:",
                key="hitl_none_text",
                height=80,
            )

        confirm_disabled = none_selected and not clarification.strip()
        if st.button("Confirm", key="hitl_confirm", type="primary", disabled=confirm_disabled):
            hitl_q.put(f"none, I meant {clarification.strip()}" if none_selected else choice)
            st.session_state.hitl_pending = None
            st.rerun()
        if confirm_disabled:
            st.caption("Please describe the package before confirming.")

    elif ptype == "package_clarification":
        st.markdown(f"### {payload.get('message', 'Clarification needed')}")
        answer = st.text_input("Your answer", key="hitl_clarify")
        if st.button("Submit", key="hitl_submit", type="primary"):
            hitl_q.put(answer)
            st.session_state.hitl_pending = None
            st.rerun()

    elif ptype == "existing_doc_found":
        _render_hitl_existing_doc(payload.get("data", {}), hitl_q)

    elif ptype == "partial_cache_found":
        _render_hitl_partial_cache(payload.get("data", {}), hitl_q)

    elif ptype == "update_assessment":
        _render_hitl_update_assessment(payload.get("data", {}), hitl_q)


def _render_hitl_existing_doc(data: dict, hitl_q: queue.Queue) -> None:
    """State A: completed documentation found. Offer View / Update / Regenerate."""
    pkg_name    = data.get("package_name", "this package")
    best        = data.get("best_match", {})
    others      = data.get("other_matches", [])
    source_tid  = best.get("thread_id", "")

    with st.container(border=True):
        st.markdown(f"### Documentation already exists for **{pkg_name}**")

        updated_at    = best.get("updated_at", "")
        quality_score = best.get("quality_score")
        chapter_count = best.get("chapter_count")
        word_count    = best.get("word_count")

        meta_parts = []
        if updated_at:
            meta_parts.append(f"Last generated: {relative_time(updated_at)}")
        if quality_score is not None:
            meta_parts.append(f"quality {quality_score:.1f}/5")
        if chapter_count:
            meta_parts.append(f"{chapter_count} chapters")
        if word_count:
            meta_parts.append(f"{word_count:,} words")
        if meta_parts:
            st.caption(" · ".join(meta_parts))

        options = [
            "View existing documentation",
            "Update documentation — check for new releases and changes",
            "Regenerate from scratch — ignore existing resources",
        ]
        choice = st.radio("What would you like to do?", options, key="hitl_cache_radio")

        if others:
            with st.expander(f"Other saved versions ({len(others)} more)"):
                for s in others:
                    cols = st.columns([2, 1, 1])
                    cols[0].caption(f"`{s.get('thread_id', '')[:8]}…`")
                    cols[1].caption(relative_time(s.get("updated_at", "")))
                    if s.get("quality_score") is not None:
                        cols[2].caption(f"Q {s['quality_score']:.1f}/5")

        if st.button("Continue", key="hitl_cache_confirm", type="primary"):
            if choice == options[0]:
                hitl_q.put({"decision": "view",       "source_thread_id": source_tid})
            elif choice == options[1]:
                hitl_q.put({"decision": "update",     "source_thread_id": source_tid})
            else:
                hitl_q.put({"decision": "regenerate", "source_thread_id": None})
            st.session_state.hitl_pending = None
            st.rerun()


def _render_hitl_partial_cache(data: dict, hitl_q: queue.Queue) -> None:
    """State B: partial resources found. Offer Resume / Regenerate."""
    pkg_name    = data.get("package_name", "this package")
    best        = data.get("best_partial", {})
    source_tid  = best.get("thread_id", "")
    last_node   = best.get("last_completed_node") or "unknown"
    updated_at  = best.get("updated_at", "")

    st.info(
        f"Partial resources found for **{pkg_name}** "
        f"(last completed node: `{last_node}`, updated {relative_time(updated_at)})."
    )
    options = [
        "Resume from existing partial resources",
        "Start fresh — discard partial resources",
    ]
    choice = st.radio("How would you like to continue?", options, key="hitl_partial_radio")

    if st.button("Continue", key="hitl_partial_confirm", type="primary"):
        if choice == options[0]:
            hitl_q.put({"decision": "use_partial",  "source_thread_id": source_tid})
        else:
            hitl_q.put({"decision": "regenerate",   "source_thread_id": None})
        st.session_state.hitl_pending = None
        st.rerun()


def _render_hitl_update_assessment(data: dict, hitl_q: queue.Queue) -> None:
    """State A → update: show change report and ask for refresh strategy."""
    pkg_name      = data.get("package_name", "this package")
    baseline_date = data.get("baseline_date", "")
    available     = data.get("update_check_available", True)
    assessment    = data.get("assessment", {})
    source_tid    = data.get("source_thread_id", "")

    st.markdown(
        f"### Changes detected for **{pkg_name}** "
        f"since {relative_time(baseline_date)}"
    )

    if not available:
        st.warning(f"Could not retrieve change data from GitHub. {assessment.get('summary', '')}")
    else:
        significance = assessment.get("significance_level", "none")
        _SIGNIFICANCE_COLORS = {
            "major": "#E24B4A",
            "minor": "#F0A500",
            "patch": "#2196F3",
            "none":  "#888888",
        }
        _SIGNIFICANCE_LABELS = {
            "major": "MAJOR UPDATE",
            "minor": "MINOR UPDATE",
            "patch": "PATCH",
            "none":  "NO CHANGES",
        }
        color = _SIGNIFICANCE_COLORS.get(significance, "#888888")
        label = _SIGNIFICANCE_LABELS.get(significance, significance.upper())
        st.markdown(
            f'<span style="background:{color};color:white;padding:3px 8px;'
            f'border-radius:4px;font-size:0.8em;font-weight:bold">{label}</span>',
            unsafe_allow_html=True,
        )
        st.markdown("")

        if summary := assessment.get("summary"):
            st.markdown(summary)

        if releases := assessment.get("new_releases", []):
            st.subheader("New releases")
            for rel in releases:
                st.markdown(f"**{rel.get('tag', '')}** — {rel.get('title', '')}")
                if hl := rel.get("highlights", ""):
                    st.caption(hl[:300])

        if breaking := assessment.get("breaking_changes", []):
            st.subheader("Breaking changes")
            for bc in breaking:
                st.markdown(f"⚠️ {bc}")

        if features := assessment.get("new_features", []):
            st.subheader("New features")
            for feat in features:
                st.markdown(f"• {feat}")

        recommendation = assessment.get("recommendation", "partial_refresh")
        if recommendation == "full_refresh":
            st.warning("Recommended: full refresh — major changes detected, existing documentation may be outdated.")
        elif recommendation == "partial_refresh":
            st.info("Recommended: partial refresh — reuse chapter structure, refresh content.")
        else:
            st.success("No significant changes. Your documentation is likely still accurate.")

    # Map recommendation to default radio index
    rec = assessment.get("recommendation", "partial_refresh")
    default_idx = 0 if rec == "full_refresh" else (2 if rec == "no_update" else 1)

    options = [
        "Proceed with full refresh — re-fetch all resources",
        "Proceed with partial refresh — reuse chapter structure where possible",
        "Cancel — keep existing documentation as-is",
    ]
    choice = st.radio(
        "How would you like to proceed?",
        options,
        index=default_idx,
        key="hitl_update_radio",
    )

    if st.button("Continue", key="hitl_update_confirm", type="primary"):
        if choice == options[0]:
            hitl_q.put({"decision": "proceed_update", "refresh_strategy": "full_refresh"})
        elif choice == options[1]:
            hitl_q.put({"decision": "proceed_update", "refresh_strategy": "partial_refresh"})
        else:
            hitl_q.put({"decision": "cancel_update", "refresh_strategy": None})
        st.session_state.hitl_pending = None
        st.rerun()


# ── 4. Final output ───────────────────────────────────────────────────────────

def render_view_existing_doc(thread_id: str) -> None:
    """
    Display a previously-generated documentation from another session.
    Called when cache_decision == "view" (user chose not to regenerate).
    """
    from src.graph.scratchpad import read_scratchpad as _rsp
    from src.graph.store import get_session_meta as _gsm

    meta      = _gsm(store, thread_id) or {}
    pkg       = meta.get("package_name", thread_id[:8])
    final_doc = _rsp(thread_id, "writer_agent")

    st.markdown(f"## Documentation: **{pkg}**")

    caption_parts: list[str] = []
    if updated_at := meta.get("updated_at"):
        caption_parts.append(f"Generated {relative_time(updated_at)}")
    if qs := meta.get("quality_score"):
        caption_parts.append(f"Quality {qs:.1f}/5")
    if cc := meta.get("chapter_count"):
        caption_parts.append(f"{cc} chapters")
    if wc := meta.get("word_count"):
        caption_parts.append(f"{wc:,} words")
    if caption_parts:
        st.caption(" · ".join(caption_parts))

    if final_doc:
        with st.expander("View documentation", expanded=True):
            st.markdown(final_doc)
        st.download_button(
            label="Download documentation (.md)",
            data=final_doc,
            file_name=f"docs_{pkg}.md",
            mime="text/markdown",
        )
    else:
        st.info(f"Documentation not found in scratchpad for session `{thread_id[:8]}`.")

    if st.button("Update this documentation", key="update_existing_doc"):
        st.session_state.force_update_thread_id = thread_id
        # Clear current active session so a new request can be started
        _clear_active_session()
        st.rerun()


def render_final_output(output_path: str, thread_id: str) -> None:
    """Display the assembled documentation and offer a download button."""
    st.markdown("---")
    st.markdown("## Documentation ready!")

    final_doc = read_scratchpad(thread_id, "writer_agent")
    if not final_doc and output_path:
        out_dir = Path(output_path)
        if out_dir.is_dir():
            parts     = [f.read_text(encoding="utf-8", errors="replace")
                         for f in sorted(out_dir.glob("*.md"))]
            final_doc = "\n\n---\n\n".join(parts)

    if final_doc:
        with st.expander("View documentation", expanded=True):
            st.markdown(final_doc)
        st.download_button(
            label="Download documentation (.md)",
            data=final_doc,
            file_name=f"docs_{thread_id[:8]}.md",
            mime="text/markdown",
        )
    else:
        st.info(f"Chapter files written to `{output_path}/`")


# ── 5. Progress fragment ──────────────────────────────────────────────────────

@st.fragment(run_every=1)
def render_progress() -> None:
    """
    Live-updating fragment that polls the progress queue every second.

    Phase 1: drain the queue and mutate pipeline_steps via process_events().
    Phase 2: render pipeline_steps (pure read).
    Phase 3: show HITL widget or error banner if needed.

    When pipeline_done fires, sets needs_rerun=True so the full page
    re-renders and switches from fragment mode to static rendering,
    allowing users to freely expand/collapse completed steps.
    """
    q: queue.Queue | None = st.session_state.get("progress_q")

    needs_rerun = False
    if q is not None:
        events: list[dict] = []
        while True:
            try:
                events.append(q.get_nowait())
            except queue.Empty:
                break
        if events:
            needs_rerun = process_events(events)

    render_pipeline_steps()

    if st.session_state.get("hitl_pending"):
        render_hitl(st.session_state.hitl_pending)

    if st.session_state.get("pipeline_error") and st.session_state.get("progress_q") is None:
        st.error(f"Pipeline error: {st.session_state.pipeline_error}")

    if needs_rerun:
        st.rerun()


# ── 6. Session views ──────────────────────────────────────────────────────────

def render_new_session_form() -> None:
    """Landing form — prompts the user to enter a package name."""
    st.markdown("## Generate documentation for a package")
    with st.form("request_form"):
        package_request = st.text_input(
            "Package name or description",
            placeholder="e.g. httpx, requests Python, fastapi",
        )
        submitted = st.form_submit_button("Generate docs", type="primary")

    if submitted and package_request.strip():
        thread_id = str(uuid.uuid4())
        st.session_state.active_thread_id = thread_id
        start_graph_thread(thread_id, st.session_state.user_id, package_request.strip())
        st.rerun()


def render_active_session() -> None:
    """Show live pipeline progress for the active session."""
    thread_id = st.session_state.active_thread_id
    inferred  = st.session_state.get("inferred_package_name")

    header = f"## Generate documentation for **{inferred}**" if inferred \
             else "## Generate documentation for a package"
    st.markdown(header)
    st.caption(f"Session: `{thread_id}`")

    if st.session_state.get("planned_chapters"):
        with st.expander("Chapter plan", expanded=False):
            for i, title in enumerate(st.session_state.planned_chapters, 1):
                st.markdown(f"**{i}.** {title}")

    if "progress_q" in st.session_state:
        # Pipeline is active — fragment polls and renders live.
        render_progress()
    elif st.session_state.get("pipeline_done"):
        # Pipeline finished — static render so steps don't auto-collapse.
        render_pipeline_steps()
        view_tid = st.session_state.get("view_doc_thread_id")
        if view_tid:
            render_view_existing_doc(view_tid)
        else:
            render_final_output(st.session_state.get("final_output_path", ""), thread_id)
    elif st.session_state.get("pipeline_error"):
        render_pipeline_steps()
        st.error(f"Pipeline error: {st.session_state.pipeline_error}")


def render_completed_session(thread_id: str) -> None:
    """Load and display a completed session's steps and documentation."""
    meta = get_session_meta(store, thread_id) or {}
    pkg  = meta.get("package_name", thread_id[:8])

    st.markdown(f"## Documentation: **{pkg}**")
    st.caption(f"Session: `{thread_id}`")

    if not st.session_state.get("pipeline_steps"):
        st.session_state.pipeline_steps = restore_steps_from_scratchpad(thread_id)

    if chapter_plan := meta.get("chapter_plan", []):
        with st.expander("Chapter plan", expanded=False):
            for i, title in enumerate(chapter_plan, 1):
                st.markdown(f"**{i}.** {title}")

    render_pipeline_steps()

    if final_doc := read_scratchpad(thread_id, "writer_agent"):
        st.markdown("---")
        with st.expander("View documentation", expanded=True):
            st.markdown(final_doc)
        st.download_button(
            label="Download (.md)",
            data=final_doc,
            file_name=f"docs_{pkg}.md",
            mime="text/markdown",
        )
    else:
        st.info("Documentation files not found in scratchpad.")

    if st.button("← Back to sessions"):
        del st.session_state["view_thread_id"]
        st.session_state.pipeline_steps = []
        st.rerun()


# ── 7. Sidebar ────────────────────────────────────────────────────────────────

def render_sidebar() -> None:
    """Render the sidebar: new-session button, status legend, and session list."""
    with st.sidebar:
        st.title("📚 DocSmith")
        st.markdown("---")

        if st.button("＋ New session", use_container_width=True):
            _clear_active_session()
            st.rerun()

        st.markdown("### Past sessions")

        with st.expander("Status legend", expanded=False):
            st.markdown(
                "🟢 **Completed** — documentation generated  \n"
                "🔵 **Running** — pipeline is active  \n"
                "🟠 **Paused** — interrupted, can be resumed  \n"
                "🔴 **Failed** — pipeline encountered an error  \n"
                "⚪ **Unknown** — status not yet recorded"
            )

        sessions = list_user_sessions(store, st.session_state.user_id)
        if not sessions:
            st.caption("No sessions yet.")
        else:
            for s in sessions[:20]:
                _render_session_card(s)


def _render_session_card(s: dict) -> None:
    status  = s.get("status", "unknown")
    icon    = STATUS_COLORS.get(status, "⚪")
    pkg     = s.get("package_name", "Unknown")
    tid     = s.get("thread_id", "")
    updated = relative_time(s.get("updated_at", ""))

    # Guard: block delete while the background thread is running for this session.
    is_active_processing = (
        tid == st.session_state.get("active_thread_id")
        and "progress_q" in st.session_state
    )

    with st.container():
        st.markdown(f"**{pkg}** {icon}")
        st.caption(updated)
        _, action_col, delete_col = st.columns([6, 3, 1])

        with action_col:
            if status in ("paused", "in_progress", "running"):
                if st.button("Resume", key=f"resume_{tid}"):
                    _clear_queue_state()
                    st.session_state.pipeline_steps   = restore_steps_from_scratchpad(tid)
                    st.session_state.pipeline_done    = False
                    st.session_state.pipeline_error   = None
                    st.session_state.active_thread_id = tid
                    start_graph_thread(tid, st.session_state.user_id, pkg)
                    st.rerun()
            elif status == "completed":
                if st.button("View", key=f"view_{tid}"):
                    st.session_state.pipeline_steps = []
                    st.session_state.view_thread_id = tid
                    st.rerun()

        with delete_col:
            if is_active_processing:
                st.markdown(
                    '<div style="text-align:center;color:#aaa;" '
                    'title="Cannot delete while processing">❌</div>',
                    unsafe_allow_html=True,
                )
            else:
                if st.button("❌", key=f"delete_{tid}", help="Delete this session"):
                    st.session_state["pending_delete_thread_id"] = tid
                    st.rerun()

        st.markdown("---")


def render_delete_confirmation() -> None:
    """
    If st.session_state["pending_delete_thread_id"] is set, render a confirmation
    dialog in the main area.  Uses @st.dialog (Streamlit ≥ 1.36) when available,
    otherwise falls back to a bordered container.
    """
    tid = st.session_state.get("pending_delete_thread_id")
    if not tid:
        return

    meta = get_session_meta(store, tid) or {}
    pkg  = meta.get("package_name", tid[:8])

    if hasattr(st, "dialog"):
        @st.dialog("Delete session?")
        def _delete_dialog() -> None:
            _render_delete_body(pkg, tid)
        _delete_dialog()
    else:
        with st.container(border=True):
            st.markdown("### Delete session?")
            _render_delete_body(pkg, tid)


def _render_delete_body(pkg: str, tid: str) -> None:
    st.markdown(
        f"This will permanently delete all scratchpad files and store entries "
        f"for **{pkg}**. This cannot be undone."
    )
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Confirm delete", type="primary", key="confirm_delete"):
            delete_session(store, tid)
            st.session_state.pop("pending_delete_thread_id", None)
            if st.session_state.get("active_thread_id") == tid:
                _clear_active_session()
            if st.session_state.get("view_thread_id") == tid:
                st.session_state.pop("view_thread_id", None)
                st.session_state.pipeline_steps = []
            st.rerun()
    with col2:
        if st.button("Cancel", key="cancel_delete"):
            st.session_state.pop("pending_delete_thread_id", None)
            st.rerun()


def _clear_active_session() -> None:
    """Reset all session-related keys when starting a new session."""
    for key in (
        "active_thread_id", "view_thread_id", "progress_q",
        "hitl_q", "hitl_pending", "pipeline_done", "pipeline_error",
        "final_output_path", "planned_chapters",
        "inferred_package_name", "pipeline_steps",
    ):
        st.session_state.pop(key, None)
    st.session_state.pipeline_done  = False
    st.session_state.pipeline_error = None


def _clear_queue_state() -> None:
    """Reset queue-related keys before resuming an existing session."""
    for key in (
        "progress_q", "hitl_q", "hitl_pending",
        "pipeline_done", "pipeline_error",
        "final_output_path", "planned_chapters",
        "inferred_package_name",
    ):
        st.session_state.pop(key, None)
