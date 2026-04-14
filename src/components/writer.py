import glob as glob_mod
from pathlib import Path

from deepagents import create_deep_agent, FilesystemPermission
from deepagents.backends import FilesystemBackend
from openai import RateLimitError
from pydantic import BaseModel
from tenacity import retry, retry_if_exception_type, wait_exponential

from src.core.llm import llm
from src.state import DocSmithState


# ── Pydantic models ───────────────────────────────────────────────────────────

class ChapterSpec(BaseModel):
    slug: str          # e.g. "01-overview" — becomes filename stem
    title: str
    description: str   # full writing brief for this chapter

class ChapterPlan(BaseModel):
    chapters: list[ChapterSpec]

class ChapterReview(BaseModel):
    accepted: bool
    notes: str         # empty string when accepted; actionable feedback when rejected


# ── Shared helpers ────────────────────────────────────────────────────────────

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


# ── Prompts ───────────────────────────────────────────────────────────────────

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
Use the read_file, glob, and grep filesystem tools to read ALL source files in the scratchpad directory.
Then write the single chapter file at the exact path provided.

QUALITY RULES:
- Every code example must be complete and runnable on its own.
- Never say "see the docs" — explain it inline.
- Use progressive disclosure: simple version first, advanced options later.
- Include type annotations in all code examples.
- The chapter file must stand alone — assume the reader may jump directly to it.
- Follow the writing instructions in the chapter description exactly."""

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
async def _invoke_agent(agent, messages: dict) -> dict:
    return await agent.ainvoke(messages)


@_retry
async def _invoke_reviewer(messages: list) -> ChapterReview:
    return await _reviewer.ainvoke(messages)


# ── Node 1: chapter_planner_node ──────────────────────────────────────────────

async def chapter_planner_node(state: DocSmithState) -> dict:
    summary = _read_scratchpad_summary(state["scratchpad_dir"])

    plan: ChapterPlan = await _planner.ainvoke([
        ("system", _PLANNER_SYSTEM_PROMPT),
        ("user",
            f"Package: {state['package_name']} ({state['language']}, {state['ecosystem']})\n\n"
            f"Quality report (gaps to address):\n{state.get('quality_report', {})}\n\n"
            f"Source material summary:\n{summary}"
        ),
    ])

    chapters_as_dicts = [c.model_dump() for c in plan.chapters]

    return {
        "chapters": chapters_as_dicts,
        "chapter_results": [],   # pre-initialize the accumulator
        "messages": [("assistant",
            f"Chapter plan ({len(plan.chapters)} chapters): "
            + ", ".join(c.title for c in plan.chapters)
        )],
    }


# ── Node 2: write_review_chapter_node ────────────────────────────────────────

async def write_review_chapter_node(state: DocSmithState) -> dict:
    chapter = ChapterSpec(**state["current_chapter"])
    scratchpad_dir = state["scratchpad_dir"]
    chapter_path = _output_dir(state) / f"{chapter.slug}.md"

    agent = create_deep_agent(
        model=llm,
        system_prompt=_WRITER_SYSTEM_PROMPT,
        permissions=[
            FilesystemPermission(operations=["read", "write"], paths=[
                str(Path(scratchpad_dir).absolute()),
                str(Path("output").absolute()),
            ]),
        ],
        backend=FilesystemBackend(),
    )

    notes = ""
    accepted = False
    iteration = 0

    for iteration in range(1, MAX_REVIEW_ITERATIONS + 1):
        retry_section = (
            f"\n\nREVIEWER FEEDBACK (iteration {iteration - 1}) — address ALL points:\n{notes}"
            if notes else ""
        )

        await _invoke_agent(agent, {"messages": [("user",
            f"Write documentation for: {state['package_name']} ({state['language']})\n"
            f"Read ALL source files from: {scratchpad_dir}\n"
            f"Chapter to write:\n"
            f"  Title: {chapter.title}\n"
            f"  Writing brief: {chapter.description}\n"
            f"  Output file: {chapter_path.absolute()}\n"
            f"{retry_section}"
        )]})

        if not chapter_path.exists() or not chapter_path.read_text(encoding="utf-8", errors="replace").strip():
            notes = f"The file at {chapter_path} is missing or empty. Write the full chapter content."
            continue

        draft = chapter_path.read_text(encoding="utf-8", errors="replace")

        review: ChapterReview = await _invoke_reviewer([
            ("user", _REVIEWER_PROMPT.format(
                description=chapter.description,
                draft=draft[:20_000],
            ))
        ])

        accepted = review.accepted
        notes = review.notes

        if accepted:
            break

    return {
        "chapter_results": [{
            "slug": chapter.slug,
            "title": chapter.title,
            "path": str(chapter_path),
            "accepted": accepted,
            "iterations": iteration,
        }],
        "messages": [("assistant",
            f"Chapter '{chapter.title}': {'accepted' if accepted else 'max iterations reached'} "
            f"after {iteration} iteration(s)."
        )],
    }


# ── Node 3: chapter_assembler_node ────────────────────────────────────────────

async def chapter_assembler_node(state: DocSmithState) -> dict:
    output_dir = _output_dir(state)

    chapter_files = sorted(output_dir.glob("*.md"))
    final_doc = "\n\n---\n\n".join(
        f.read_text(encoding="utf-8", errors="replace") for f in chapter_files
    )

    results = state.get("chapter_results", [])
    accepted_count = sum(1 for r in results if r.get("accepted"))
    total = len(results)

    return {
        "final_documentation": final_doc,
        "output_file": str(output_dir),
        "messages": [("assistant",
            f"Documentation assembled: {len(chapter_files)} chapter files in `{output_dir}/`. "
            f"Review quality: {accepted_count}/{total} chapters accepted on first pass."
        )],
    }
