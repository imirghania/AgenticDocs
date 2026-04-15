from langgraph.graph import StateGraph, START, END
from langgraph.types import Send

from src.state import DocSmithState
from src.components.resumption_inspector import resumption_inspector_node
from src.components.intent_parser import intent_parser_node
from src.components.web_discovery import web_discovery_node
from src.components.confirm_package import confirm_package_node
from src.components.local_cache_inspector import local_cache_inspector_node
from src.components.docs_discovery import docs_discovery_node
from src.components.context7_agent import context7_node
from src.components.docs_scraper import docs_scraper_node
from src.components.github_agent import github_agent_node
from src.components.quality_judge import quality_judge_node, quality_router
from src.components.enrichment import enrichment_node
from src.agents.chapter_planner import chapter_planner_node
from src.agents.chapter_crossref import chapter_crossref_node
from src.components.writer import (
    write_review_chapter_node,
    chapter_assembler_node,
)


def get_checkpointer():
    """
    Return an async-compatible checkpointer (MemorySaver).

    AsyncSqliteSaver / AsyncPostgresSaver both require instantiation inside a
    running event loop, which is unavailable at Streamlit startup time.
    MemorySaver is fully async-compatible (implements aget/aput/aput_writes)
    and is sufficient here because:
      - HITL state persists within a Streamlit session (graph is cached in
        st.session_state alongside the saver).
      - Cross-restart resumption is handled by scratchpad files, not the
        checkpointer.
    """
    from langgraph.checkpoint.memory import MemorySaver
    return MemorySaver()


def build_graph():
    builder = StateGraph(DocSmithState)

    # ── Node registrations ────────────────────────────────────────────────────

    builder.add_node("resumption_inspector",  resumption_inspector_node)  # always first
    builder.add_node("intent_parser",         intent_parser_node)
    builder.add_node("web_discovery",         web_discovery_node)
    builder.add_node("confirm_package",       confirm_package_node)
    builder.add_node("local_cache_inspector", local_cache_inspector_node)
    builder.add_node("end_view",              lambda s: {})  # pass-through; routes to END
    builder.add_node("docs_discovery",        docs_discovery_node)
    builder.add_node("context7_agent",       context7_node)
    builder.add_node("docs_scraper",         docs_scraper_node)
    builder.add_node("github_agent",         github_agent_node)
    builder.add_node("aggregator",           lambda s: s)   # pass-through fan-in sync node
    builder.add_node("quality_judge",        quality_judge_node)
    builder.add_node("enrichment_agent",     enrichment_node)
    builder.add_node("chapter_planner",      chapter_planner_node)
    builder.add_node("write_review_chapter", write_review_chapter_node)
    builder.add_node("chapter_crossref",     chapter_crossref_node)
    builder.add_node("chapter_assembler",    chapter_assembler_node)

    # ── Edges: discovery phase ────────────────────────────────────────────────

    builder.add_edge(START,                   "resumption_inspector")
    builder.add_edge("resumption_inspector",  "intent_parser")
    builder.add_edge("intent_parser",         "web_discovery")
    builder.add_edge("web_discovery",         "confirm_package")
    builder.add_edge("confirm_package",       "local_cache_inspector")

    # ── Cache decision routing ────────────────────────────────────────────────
    # "view"  → end_view → END  (existing doc displayed, no ingestion)
    # else    → docs_discovery  (full / partial / update pipeline)

    def _cache_router(state: DocSmithState) -> str:
        return "end_view" if state.get("cache_decision") == "view" else "docs_discovery"

    builder.add_conditional_edges(
        "local_cache_inspector",
        _cache_router,
        {"end_view": "end_view", "docs_discovery": "docs_discovery"},
    )
    builder.add_edge("end_view", END)

    # ── Edges: parallel ingestion fan-out ─────────────────────────────────────

    def fan_out_ingestion(state: DocSmithState):
        return [
            Send("context7_agent", state),
            Send("docs_scraper",   state),
            Send("github_agent",   state),
        ]

    builder.add_conditional_edges("docs_discovery", fan_out_ingestion)

    # ── Edges: fan-in → quality gate ─────────────────────────────────────────

    builder.add_edge("context7_agent", "aggregator")
    builder.add_edge("docs_scraper",   "aggregator")
    builder.add_edge("github_agent",   "aggregator")
    builder.add_edge("aggregator",     "quality_judge")

    # ── Edges: conditional quality routing ───────────────────────────────────

    builder.add_conditional_edges("quality_judge", quality_router, {
        "enrichment_agent": "enrichment_agent",
        "chapter_planner":  "chapter_planner",
    })
    builder.add_edge("enrichment_agent", "chapter_planner")

    # ── Edges: parallel chapter writing fan-out ───────────────────────────────

    def fan_out_chapters(state: DocSmithState):
        return [
            Send("write_review_chapter", {**state, "current_chapter": chapter})
            for chapter in state["chapters"]
        ]

    builder.add_conditional_edges("chapter_planner", fan_out_chapters)

    # ── Edges: fan-in → cross-reference → assembly ───────────────────────────

    builder.add_edge("write_review_chapter", "chapter_crossref")
    builder.add_edge("chapter_crossref",     "chapter_assembler")
    builder.add_edge("chapter_assembler",    END)

    return builder.compile(checkpointer=get_checkpointer())


graph = build_graph()
