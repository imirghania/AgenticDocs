from pydantic import BaseModel, Field

from src.core.llm import llm
from src.state import DocSmithState


class DimensionScore(BaseModel):
    score: int = Field(ge=1, le=5)
    reasoning: str
    gaps: list[str]


class DocEvaluation(BaseModel):
    beginner_friendliness: DimensionScore
    api_coverage: DimensionScore
    code_example_quality: DimensionScore
    progressive_structure: DimensionScore
    overall_score: float   # computed: mean of all dimensions
    needs_enrichment: bool # True if any dimension scores 1-2


DIMENSIONS = {
    "beginner_friendliness": "Does it explain prerequisites, provide a quickstart, and avoid unexplained jargon?",
    "api_coverage": "Does it document all major public APIs with descriptions, parameters, and types?",
    "code_example_quality": "Does it include runnable examples covering all major features and real-world patterns?",
    "progressive_structure": "Does it build from beginner → intermediate → advanced with clear concept dependencies?",
}

_evaluator = llm.with_structured_output(DimensionScore)

_EVALUATOR_PROMPT = """Evaluate this documentation on: {eval_dim}.
Criteria: {criteria}
Score 1-5. Reason first, then give score and gaps.

DOCUMENTATION:
{combined}"""

def quality_judge_node(state: DocSmithState) -> dict:
    # Combine all ingested context
    combined = "\n\n---\n\n".join(
        state.get("context7_docs", []) +
        state.get("scraped_docs", []) +
        state.get("github_content", [])
    )[:100_000]  # stay within context window

    scores = {}
    for dim, criteria in DIMENSIONS.items():
        result = _evaluator.invoke([
            ("user",
            _EVALUATOR_PROMPT.format(
                eval_dim=dim.replace("_", " ").title(),
                criteria=criteria,
                combined=combined[:25_000]
                )
            )
        ])
        scores[dim] = result

    overall = sum(s.score for s in scores.values()) / len(scores)
    needs_enrichment = any(s.score <= 2 for s in scores.values())

    return {
        "quality_score": overall / 5.0,  # normalize 0-1
        "quality_report": scores,
        "messages": [("assistant", f"Quality score: {overall:.1f}/5. {'Enrichment needed.' if needs_enrichment else 'Proceeding to writing.'}")]
    }


def quality_router(state: DocSmithState) -> str:
    """Route to enrichment if score is below threshold, otherwise go straight to writer."""
    if state.get("quality_score", 1.0) < 0.7:
        return "enrichment_agent"
    return "writer_agent"
