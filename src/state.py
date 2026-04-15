from typing import Annotated, Optional
from langgraph.graph import MessagesState
import operator


def _union_sets(a: set, b: set) -> set:
    return a | b


class DocSmithState(MessagesState):
    # Session identity (set before graph invocation)
    thread_id: str
    user_id: str

    # Phase 1 — Discovery
    package_name: str
    language: str
    ecosystem: str                          # e.g. "pypi", "npm", "cargo"
    search_results: list[dict]              # raw Tavily results
    confirmed_package: Optional[dict]       # user-confirmed result
    github_url: Optional[str]
    docs_url: Optional[str]

    # Phase 2 — Ingestion (disk-backed scratchpad, no in-memory content caps)
    scratchpad_dir: Optional[str]                          # per-run scratch directory
    scratchpad_files: Annotated[list[str], operator.add]   # paths written by agents

    # Phase 3 — Evaluation
    quality_score: Optional[float]
    quality_report: Optional[dict]          # maps dimension name → DimensionScore

    # Phase 4 — Writing
    chapters: Optional[list[dict]]                          # planned ChapterSpec dicts from planner
    current_chapter: Optional[dict]                         # set per Send invocation; not used after fan-in
    chapter_results: Annotated[list[dict], operator.add]    # accumulates one entry per chapter worker
    chapter_plan: list[str]                                  # chapter titles in order (from chapter_planner)
    final_documentation: Optional[str]
    output_file: Optional[str]             # path to output/{package}/ directory

    # Resumption support
    completed_nodes: Annotated[set[str], _union_sets]   # set-union reducer
    is_resuming: bool
    resumption_summary: str
