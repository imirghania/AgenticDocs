from typing import Annotated, Optional
from langgraph.graph import MessagesState
import operator

class DocSmithState(MessagesState):
    # Phase 1 — Discovery
    package_name: str
    language: str
    ecosystem: str                          # e.g. "pypi", "npm", "cargo"
    search_results: list[dict]              # raw Tavily results
    confirmed_package: Optional[dict]       # user-confirmed result
    github_url: Optional[str]
    docs_url: Optional[str]

    # Phase 2 — Ingestion (parallel, merged with operator.add)
    context7_docs: Annotated[list[str], operator.add]
    scraped_docs: Annotated[list[str], operator.add]
    github_content: Annotated[list[str], operator.add]

    # Phase 3 — Evaluation
    quality_score: Optional[float]
    quality_report: Optional[dict]          # full Pydantic evaluation object

    # Phase 4 — Writing
    final_documentation: Optional[str]