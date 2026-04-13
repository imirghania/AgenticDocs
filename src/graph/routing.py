from typing import Literal
from src.state import DocSmithState


def quality_router(state: DocSmithState) -> Literal["enrichment_agent", "writer_agent"]:
    score = state.get("quality_score", 0)
    return "enrichment_agent" if score < 0.7 else "writer_agent"