"""
DocSmith — Streamlit entry point.

Run with:  streamlit run streamlit_app.py

Module layout
─────────────
ui/constants.py       Node labels, pipeline order, status colours, CSS
ui/step_formatter.py  format_step_output() — one _fmt_* per node
ui/graph_runner.py    Background thread + async graph runner
ui/event_processor.py process_events() — mutates pipeline_steps
ui/rendering.py       All st.* functions: steps, HITL, views, sidebar
ui/session.py         user_id persistence, scratchpad restore, time utils
"""

import streamlit as st

from src.graph.orchestrator import build_graph
from ui.constants import PIPELINE_CSS
from ui.rendering import (
    render_active_session,
    render_completed_session,
    render_new_session_form,
    render_sidebar,
)
from ui.session import get_or_create_user_id


def main() -> None:
    st.set_page_config(page_title="DocSmith", page_icon="📚", layout="wide")
    st.markdown(PIPELINE_CSS, unsafe_allow_html=True)

    # ── One-time session state initialisation ─────────────────────────────────
    if "user_id" not in st.session_state:
        st.session_state.user_id = get_or_create_user_id()
    if "graph" not in st.session_state:
        st.session_state.graph = build_graph()
    if "pipeline_steps" not in st.session_state:
        st.session_state.pipeline_steps = []
    if "planned_chapters" not in st.session_state:
        st.session_state.planned_chapters = []
    if "pipeline_done" not in st.session_state:
        st.session_state.pipeline_done = False
    if "pipeline_error" not in st.session_state:
        st.session_state.pipeline_error = None

    render_sidebar()

    # ── Route to the appropriate main-area view ───────────────────────────────
    if "view_thread_id" in st.session_state:
        render_completed_session(st.session_state.view_thread_id)
    elif "active_thread_id" not in st.session_state:
        render_new_session_form()
    else:
        render_active_session()


if __name__ == "__main__":
    main()
