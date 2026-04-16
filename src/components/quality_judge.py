import asyncio
import json
import glob as glob_mod
from pathlib import Path

from tenacity import retry, retry_if_exception, wait_exponential

from src.core.llm import llm
from src.graph.resumption import skippable
from src.graph.scratchpad import write_scratchpad
from src.prompts.quality import EVALUATOR_PROMPT
from src.schemas.quality import DimensionScore
from src.state import AgenticDocsState


def _is_rate_limit_error(exc: BaseException) -> bool:
    """Return True for HTTP 429 / rate-limit errors from any LLM provider."""
    type_name = type(exc).__name__
    # openai.RateLimitError, anthropic.RateLimitError, httpx.HTTPStatusError w/ 429
    if "RateLimitError" in type_name:
        return True
    if "HTTPStatusError" in type_name:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        return status == 429
    # Fallback: check string representation for common 429 phrases
    return "429" in str(exc) or "rate limit" in str(exc).lower()


DIMENSIONS = {
    "beginner_friendliness": "Does it explain prerequisites, provide a quickstart, and avoid unexplained jargon?",
    "api_coverage": "Does it document all major public APIs with descriptions, parameters, and types?",
    "code_example_quality": "Does it include runnable examples covering all major features and real-world patterns?",
    "progressive_structure": "Does it build from beginner → intermediate → advanced with clear concept dependencies?",
}

_evaluator = llm.with_structured_output(DimensionScore)


def _read_scratchpad(scratchpad_dir: str) -> str:
    files = sorted(glob_mod.glob(f"{scratchpad_dir}/*.md"))
    return "\n\n---\n\n".join(
        Path(f).read_text(encoding="utf-8", errors="replace") for f in files
    )


@skippable("quality_judge")
async def quality_judge_node(state: AgenticDocsState) -> dict:
    combined = _read_scratchpad(state["scratchpad_dir"])
    sliced = combined[:25_000]  # per-eval slice only

    @retry(
        retry=retry_if_exception(_is_rate_limit_error),
        wait=wait_exponential(multiplier=1, min=10, max=90),
    )
    async def _evaluate(dim: str, criteria: str) -> tuple[str, DimensionScore]:
        result = await _evaluator.ainvoke([
            ("user",
            EVALUATOR_PROMPT.format(
                eval_dim=dim.replace("_", " ").title(),
                criteria=criteria,
                combined=sliced,
            ))
        ])
        return dim, result

    tasks = await asyncio.gather(*[
        _evaluate(dim, criteria) for dim, criteria in DIMENSIONS.items()
    ])
    scores: dict[str, DimensionScore] = dict(tasks)

    overall = sum(s.score for s in scores.values()) / len(scores)
    needs_enrichment = any(s.score <= 2 for s in scores.values())

    # Serialize for JSON storage (DimensionScore → dict)
    serialized_report = {k: v.model_dump() for k, v in scores.items()}

    write_scratchpad(
        state["thread_id"],
        "quality_judge",
        json.dumps({"quality_score": overall / 5.0, "quality_report": serialized_report}, indent=2),
    )

    # One message per dimension so the UI can show each check result
    dimension_messages = [
        ("assistant",
        f"[Quality] {dim.replace('_', ' ').title()}: {score.score:.1f}/5 — "
        f"{score.reasoning[:120]}"
        + (f" | Gaps: {', '.join(score.gaps[:2])}" if score.gaps else "")
        )
        for dim, score in scores.items()
    ]
    summary_msg = (
        "assistant",
        f"Overall quality: {overall:.1f}/5. "
        f"{'Routing to enrichment.' if needs_enrichment else 'Proceeding to chapter planner.'}",
    )

    return {
        "quality_score": overall / 5.0,
        "quality_report": serialized_report,
        "messages": dimension_messages + [summary_msg],
    }


def quality_router(state: AgenticDocsState) -> str:
    """Route to enrichment if score is below threshold, otherwise go straight to chapter planner."""
    if state.get("quality_score", 1.0) < 0.7:
        return "enrichment_agent"
    return "chapter_planner"
