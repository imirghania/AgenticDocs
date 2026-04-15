"""
Writer nodes for DocSmith.

chapter_planner_node has been moved to src/agents/chapter_planner.py.
This module retains the shared models, helpers, and prompts that the
chapter_planner agent imports, plus the two remaining nodes:
  - write_review_chapter_node  (fan-out worker, not individually skippable)
  - chapter_assembler_node     (@skippable("writer_agent"))
"""
import glob as glob_mod
from pathlib import Path

from openai import RateLimitError
from pydantic import BaseModel
from tenacity import retry, retry_if_exception_type, wait_exponential

from src.core.llm import llm
from src.graph.resumption import skippable
from src.graph.scratchpad import write_scratchpad
from src.state import DocSmithState


# ── Pydantic models (also imported by src/agents/chapter_planner.py) ──────────

class ChapterSpec(BaseModel):
    slug: str          # e.g. "01-overview" — becomes filename stem
    title: str
    description: str   # full writing brief for this chapter


class ChapterPlan(BaseModel):
    chapters: list[ChapterSpec]


class ChapterReview(BaseModel):
    accepted: bool
    notes: str         # empty string when accepted; actionable feedback when rejected


# ── Shared helpers (also imported by src/agents/) ────────────────────────────

def _output_dir(state: DocSmithState) -> Path:
    slug = state["package_name"].lower().replace(" ", "_").replace("/", "_")
    p = Path("output") / slug
    p.mkdir(parents=True, exist_ok=True)
    return p


def _read_scratchpad_summary(scratchpad_dir: str, max_chars: int = 12_000) -> str:
    files = sorted(glob_mod.glob(f"{scratchpad_dir}/*.md"))
    parts = []
    for f in files:
        content = Path(f).read_text(encoding="utf-8", errors="replace")
        parts.append(f"## {Path(f).name}\n{content[:3_000]}")
    combined = "\n\n---\n\n".join(parts)
    return combined[:max_chars]


# ── Prompts (also imported by src/agents/chapter_planner.py) ─────────────────

_PLANNER_SYSTEM_PROMPT = """You are a documentation architect. You receive a summary of raw
source material (README, API references, examples, GitHub source) for a software package.
Your job is to design the optimal chapter structure for comprehensive developer documentation.

Rules for chapters:
- Between 5 and 10 chapters.
- Slugs must be lowercase, hyphen-separated, and prefixed with zero-padded numbers (e.g. "01-overview", "02-installation").
- Each description is a detailed writing brief (3-6 sentences) that the writer will follow exactly.
- Cover: conceptual overview, installation, quickstart, core concepts, API reference,
  real-world use cases, common patterns, troubleshooting. Adapt, merge, or split based
  on what the source material actually contains.
- Never invent chapters about content that does not appear in the source material."""

_WRITER_SYSTEM_PROMPT = """You are a world-class technical documentation writer.
You will receive summarised source material (README, API docs, GitHub code, examples) for a
software package, along with a specific chapter title and detailed writing brief.
Write the chapter content as a complete, well-structured Markdown document.

QUALITY RULES:
- Every code example must be complete and runnable on its own.
- Never say "see the docs" — explain it inline.
- Use progressive disclosure: simple version first, advanced options later.
- Include type annotations in all code examples.
- The chapter must stand alone — assume the reader may jump directly to it.
- Follow the writing brief exactly; cover every point it mentions.
- Output ONLY the Markdown content — no preamble, no explanation, no code fences wrapping the whole doc."""

_REVIEWER_PROMPT = """You are a senior technical documentation reviewer.
Read the chapter draft carefully and evaluate it against these criteria:
- All code examples are complete and runnable.
- No unexplained jargon.
- Follows the writing brief exactly.
- Accurate — no hallucinated API names or parameters.
- Self-contained — reader can understand without reading other chapters.

Writing brief: {description}

Chapter draft:
{draft}

Return accepted=True if the chapter meets all criteria. Otherwise return accepted=False
with specific, actionable notes the writer must address."""


# ── Evaluators ────────────────────────────────────────────────────────────────

_planner = llm.with_structured_output(ChapterPlan)
_reviewer = llm.with_structured_output(ChapterReview)

MAX_REVIEW_ITERATIONS = 3

_retry = retry(
    retry=retry_if_exception_type(RateLimitError),
    wait=wait_exponential(multiplier=1, min=10, max=90),
)


@_retry
async def _invoke_writer(messages: list) -> str:
    """Call the LLM directly to generate chapter content. Returns the raw text."""
    response = await llm.ainvoke(messages)
    return response.content if hasattr(response, "content") else str(response)


@_retry
async def _invoke_reviewer(messages: list) -> ChapterReview:
    return await _reviewer.ainvoke(messages)


# ── Node: write_review_chapter_node ──────────────────────────────────────────
# Not individually skippable (fan-out). Only chapter_assembler is skippable.

async def write_review_chapter_node(state: DocSmithState) -> dict:
    chapter      = ChapterSpec(**state["current_chapter"])
    chapter_path = _output_dir(state) / f"{chapter.slug}.md"

    # Summarised source material — capped so every chapter gets the same context
    # regardless of how large individual scratchpad files are (e.g. github dump).
    source_summary = _read_scratchpad_summary(state["scratchpad_dir"])

    notes    = ""
    accepted = False
    iteration = 0

    for iteration in range(1, MAX_REVIEW_ITERATIONS + 1):
        feedback_section = (
            f"\n\nREVIEWER FEEDBACK (iteration {iteration - 1}) — address ALL points:\n{notes}"
            if notes else ""
        )

        # Prepend update context when this is a documentation update run
        writer_system = _WRITER_SYSTEM_PROMPT
        if state.get("is_update") and state.get("previous_doc_summary"):
            writer_system = (
                "You are updating existing documentation. A summary of the previous version "
                "is provided below. Acknowledge the update at the top of the output with a brief "
                "'What changed in this version' section before the main content. Highlight new "
                "features and any breaking changes prominently.\n\n"
                f"Previous documentation summary:\n{state['previous_doc_summary']}\n\n"
            ) + writer_system

        content = await _invoke_writer([
            ("system", writer_system),
            ("user",
             f"Package: {state['package_name']} ({state['language']}, {state['ecosystem']})\n\n"
             f"Chapter title: {chapter.title}\n"
             f"Writing brief: {chapter.description}\n\n"
             f"Source material (summarised):\n{source_summary}"
             f"{feedback_section}"
             ),
        ])

        content = content.strip()
        if content:
            chapter_path.write_text(content, encoding="utf-8")

        if not chapter_path.exists() or not chapter_path.read_text(encoding="utf-8", errors="replace").strip():
            notes = "Chapter content was empty. Write a complete, well-structured chapter."
            continue

        draft = chapter_path.read_text(encoding="utf-8", errors="replace")

        review: ChapterReview = await _invoke_reviewer([
            ("user", _REVIEWER_PROMPT.format(
                description=chapter.description,
                draft=draft[:20_000],
            ))
        ])

        accepted = review.accepted
        notes    = review.notes

        if accepted:
            break

    return {
        "chapter_results": [{
            "slug":      chapter.slug,
            "title":     chapter.title,
            "path":      str(chapter_path),
            "accepted":  accepted,
            "iterations": iteration,
        }],
        "messages": [("assistant",
            f"Chapter '{chapter.title}': {'accepted' if accepted else 'max iterations reached'} "
            f"after {iteration} iteration(s)."
        )],
    }


# ── Node: chapter_assembler_node ─────────────────────────────────────────────

@skippable("writer_agent")
async def chapter_assembler_node(state: DocSmithState) -> dict:
    output_dir = _output_dir(state)

    chapter_files = sorted(output_dir.glob("*.md"))
    final_doc = "\n\n---\n\n".join(
        f.read_text(encoding="utf-8", errors="replace") for f in chapter_files
    )

    results = state.get("chapter_results", [])
    accepted_count = sum(1 for r in results if r.get("accepted"))
    total = len(results)

    write_scratchpad(state["thread_id"], "writer_agent", final_doc)

    return {
        "final_documentation": final_doc,
        "output_file": str(output_dir),
        "messages": [("assistant",
            f"Documentation assembled: {len(chapter_files)} chapter files in `{output_dir}/`. "
            f"Review quality: {accepted_count}/{total} chapters accepted on first pass."
        )],
    }
