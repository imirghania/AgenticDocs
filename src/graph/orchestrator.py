from langgraph.graph import StateGraph, START, END
from langgraph.types import Send
from langgraph.checkpoint.memory import InMemorySaver
from src.state import DocSmithState
from src.components.intent_parser import intent_parser_node
from src.components.web_discovery import web_discovery_node
from src.components.confirm_package import confirm_package_node
from src.components.docs_discovery import docs_discovery_node
from src.components.context7_agent import context7_node
from src.components.docs_scraper import docs_scraper_node
from src.components.github_agent import github_agent_node
from src.components.quality_judge import quality_judge_node, quality_router
from src.components.enrichment import enrichment_node
from src.components.writer import (
    chapter_planner_node,
    write_review_chapter_node,
    chapter_assembler_node,
)


def build_graph():
    builder = StateGraph(DocSmithState)

    # Register all nodes
    builder.add_node("intent_parser",        intent_parser_node)
    builder.add_node("web_discovery",        web_discovery_node)
    builder.add_node("confirm_package",      confirm_package_node)
    builder.add_node("docs_discovery",       docs_discovery_node)
    builder.add_node("context7_agent",       context7_node)
    builder.add_node("docs_scraper",         docs_scraper_node)
    builder.add_node("github_agent",         github_agent_node)
    builder.add_node("aggregator",           lambda s: s)  # pass-through sync node
    builder.add_node("quality_judge",        quality_judge_node)
    builder.add_node("enrichment_agent",     enrichment_node)
    builder.add_node("chapter_planner",      chapter_planner_node)
    builder.add_node("write_review_chapter", write_review_chapter_node)
    builder.add_node("chapter_assembler",    chapter_assembler_node)

    # Sequential: discovery phase
    builder.add_edge(START, "intent_parser")
    builder.add_edge("intent_parser",   "web_discovery")
    builder.add_edge("web_discovery",   "confirm_package")
    builder.add_edge("confirm_package", "docs_discovery")

    # Parallel fan-out: ingestion phase
    def fan_out_ingestion(state: DocSmithState):
        return [
            Send("context7_agent", state),
            Send("docs_scraper",   state),
            Send("github_agent",   state),
        ]
    builder.add_conditional_edges("docs_discovery", fan_out_ingestion)

    # Converge back to aggregator → quality gate
    builder.add_edge("context7_agent", "aggregator")
    builder.add_edge("docs_scraper",   "aggregator")
    builder.add_edge("github_agent",   "aggregator")
    builder.add_edge("aggregator",     "quality_judge")

    # Conditional routing: quality gate → enrichment or chapter planner
    builder.add_conditional_edges("quality_judge", quality_router, {
        "enrichment_agent": "enrichment_agent",
        "chapter_planner":  "chapter_planner",
    })
    builder.add_edge("enrichment_agent", "chapter_planner")

    # Parallel fan-out: one writer+reviewer per chapter
    def fan_out_chapters(state: DocSmithState):
        return [
            Send("write_review_chapter", {**state, "current_chapter": chapter})
            for chapter in state["chapters"]
        ]
    builder.add_conditional_edges("chapter_planner", fan_out_chapters)

    # Fan-in: all chapter workers converge to assembler
    builder.add_edge("write_review_chapter", "chapter_assembler")
    builder.add_edge("chapter_assembler", END)

    # Compile with checkpointer (required for human-in-the-loop interrupt)
    return builder.compile(checkpointer=InMemorySaver())

graph = build_graph()
