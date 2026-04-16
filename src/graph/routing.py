from typing import Literal
from src.state import AgenticDocsState


def quality_router(state: AgenticDocsState) -> Literal["enrichment_agent", "chapter_planner"]:
    score = state.get("quality_score", 0)
    return "enrichment_agent" if score < 0.7 else "chapter_planner"