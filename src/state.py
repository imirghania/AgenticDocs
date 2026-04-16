from typing import Annotated, Optional
from langgraph.graph import MessagesState
import operator


def _union_sets(a: set, b: set) -> set:
    return a | b


def merge_dicts(a: dict | None, b: dict | None) -> dict:  # type: ignore[type-arg]
    merged: dict = dict(a or {})  # type: ignore[type-arg]
    merged.update(b or {})
    return merged


class AgenticDocsState(MessagesState):
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

    # Part 1 — Key terms and analogies (accumulated across fan-out chapters)
    defined_terms: Annotated[dict[str, str], merge_dicts]         # term (lowercase) → definition
    chapter_analogies: Annotated[dict[str, list[str]], merge_dicts]  # chapter_title → analogy texts

    # Part 2 — Review tracking
    chapter_review_results: Annotated[dict[str, dict], merge_dicts]  # type: ignore[type-arg]  # chapter_title → reviewer JSON
    chapters_revised: Annotated[list[str], operator.add]             # titles that needed a revision pass

    # Part 3 — Cross-reference outputs (set once by chapter_crossref_node)
    concept_index: Optional[dict[str, str]]       # term → chapter_title where first defined
    chapter_transitions: Optional[dict[str, str]] # from_chapter_title → transition paragraph
    reading_guide: Optional[str]                  # "## How to read this documentation" markdown

    # Cache / update detection (local_cache_inspector)
    cache_decision: Optional[str]             # "view"|"regenerate"|"use_partial"|"full_refresh"|"partial_refresh"
    cache_source_thread_id: Optional[str]     # thread_id of the session being reused/updated
    refresh_strategy: Optional[str]           # "full_refresh"|"partial_refresh"
    is_update: Optional[bool]                 # True when updating existing docs
    previous_doc_summary: Optional[str]       # first 2000 chars of source session's final output
    update_assessment: Optional[dict]         # parsed LLM assessment JSON from update check
