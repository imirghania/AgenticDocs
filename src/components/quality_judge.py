import asyncio
import glob as glob_mod
from pathlib import Path

from openai import RateLimitError
from pydantic import BaseModel, Field
from tenacity import retry, retry_if_exception_type, wait_exponential

from src.core.llm import llm
from src.state import DocSmithState


class DimensionScore(BaseModel):
    score: float = Field(ge=1, le=5)
    reasoning: str
    gaps: list[str]


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


def _read_scratchpad(scratchpad_dir: str) -> str:
    files = sorted(glob_mod.glob(f"{scratchpad_dir}/*.md"))
    return "\n\n---\n\n".join(
        Path(f).read_text(encoding="utf-8", errors="replace") for f in files
    )


async def quality_judge_node(state: DocSmithState) -> dict:
    combined = _read_scratchpad(state["scratchpad_dir"])
    sliced = combined[:25_000]  # per-eval slice only

    @retry(
        retry=retry_if_exception_type(RateLimitError),
        wait=wait_exponential(multiplier=1, min=10, max=90),
    )
    async def _evaluate(dim: str, criteria: str) -> tuple[str, DimensionScore]:
        result = await _evaluator.ainvoke([
            ("user",
            _EVALUATOR_PROMPT.format(
                eval_dim=dim.replace("_", " ").title(),
                criteria=criteria,
                combined=sliced,
            ))
        ])
        return dim, result

    tasks = await asyncio.gather(*[
        _evaluate(dim, criteria) for dim, criteria in DIMENSIONS.items()
    ])
    scores = dict(tasks)

    overall = sum(s.score for s in scores.values()) / len(scores)
    needs_enrichment = any(s.score <= 2 for s in scores.values())

    return {
        "quality_score": overall / 5.0,
        "quality_report": scores,
        "messages": [("assistant", f"Quality score: {overall:.1f}/5. {'Enrichment needed.' if needs_enrichment else 'Proceeding to writing.'}")]
    }


def quality_router(state: DocSmithState) -> str:
    """Route to enrichment if score is below threshold, otherwise go straight to chapter planner."""
    if state.get("quality_score", 1.0) < 0.7:
        return "enrichment_agent"
    return "chapter_planner"
