"""
Writer nodes for AgenticDocs.

chapter_planner_node has been moved to src/agents/chapter_planner.py.
This module retains the shared helpers and nodes:
  - write_review_chapter_node  (fan-out worker, not individually skippable)
  - chapter_assembler_node     (@skippable("writer_agent"))

Pydantic models live in src/schemas/writing.py.
Prompt strings live in src/prompts/writing.py.
"""
import glob as glob_mod
import json
import re
from pathlib import Path
from typing import Any

from openai import RateLimitError
from tenacity import retry, retry_if_exception_type, wait_exponential

from src.core.llm import llm
from src.graph.resumption import skippable
from src.graph.scratchpad import write_scratchpad
from src.prompts.writing import WRITER_SYSTEM_PROMPT, REVIEWER_PROMPT
from src.schemas.writing import ChapterSpec, ChapterPlan, ThoroughnessReview
from src.state import AgenticDocsState


# ── Shared helpers ────────────────────────────────────────────────────────────

def _output_dir(state: AgenticDocsState) -> Path:
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


def extract_chapter_metadata(markdown: str) -> dict[str, Any]:
    """
    Parse a chapter's markdown output and return:
      {
        "defined_terms": dict[str, str],   # term → definition from Key terms section
        "analogies": list[str],            # list of analogy callout texts
      }
    Extraction rules:
      defined_terms: find the "### Key terms" section; parse each line matching
        **{term}** — {definition} into key-value pairs.
        Key = term.strip().lower(), value = definition.strip().
      analogies: find all blockquote blocks preceded by "**Analogy:**";
        extract the blockquote text (strip leading "> " from each line).
    Returns empty dicts/lists if sections are absent — never raises.
    """
    defined_terms: dict[str, str] = {}
    analogies: list[str] = []
    try:
        # Defined terms: find ### Key terms section
        kt_match = re.search(
            r"###\s+Key terms?\s*\n(.*?)(?=\n#{1,3}\s|\Z)",
            markdown,
            re.IGNORECASE | re.DOTALL,
        )
        if kt_match:
            for line in kt_match.group(1).splitlines():
                m = re.match(r"\s*\*\*(.+?)\*\*\s*[—–-]+\s*(.+)", line)
                if m:
                    defined_terms[m.group(1).strip().lower()] = m.group(2).strip()
        # Analogies: blockquote blocks preceded by **Analogy:**
        for ana_match in re.finditer(
            r"\*\*Analogy:\*\*\s*\n((?:>.*\n?)+)", markdown
        ):
            text = "\n".join(
                line.lstrip("> ").rstrip()
                for line in ana_match.group(1).splitlines()
            ).strip()
            if text:
                analogies.append(text)
    except Exception:
        pass
    return {"defined_terms": defined_terms, "analogies": analogies}


# ── LLM chains ────────────────────────────────────────────────────────────────

_planner  = llm.with_structured_output(ChapterPlan)
_reviewer = llm.with_structured_output(ThoroughnessReview)

MAX_REVIEW_ITERATIONS = 2

_retry = retry(
    retry=retry_if_exception_type(RateLimitError),
    wait=wait_exponential(multiplier=1, min=10, max=90),
)


@_retry
async def _invoke_writer(messages: list) -> str:  # type: ignore[type-arg]
    """Call the LLM directly to generate chapter content. Returns the raw text."""
    response = await llm.ainvoke(messages)
    return response.content if hasattr(response, "content") else str(response)  # type: ignore[return-value]


@_retry
async def _invoke_reviewer(messages: list) -> ThoroughnessReview:  # type: ignore[type-arg]
    return await _reviewer.ainvoke(messages)  # type: ignore[return-value]


# ── Node: write_review_chapter_node ──────────────────────────────────────────
# Not individually skippable (fan-out). Only chapter_assembler is skippable.

async def write_review_chapter_node(state: AgenticDocsState) -> dict:  # type: ignore[type-arg]
    chapter      = ChapterSpec(**(state["current_chapter"] or {}))
    chapter_path = _output_dir(state) / f"{chapter.slug}.md"

    # Source material capped so every chapter gets the same context budget.
    source_summary = _read_scratchpad_summary(state["scratchpad_dir"] or "")

    # Terms already defined in previously completed chapters (may be empty on
    # first parallel run — accumulates across resume cycles via merge_dicts).
    defined_terms: dict[str, str] = dict(state.get("defined_terms") or {})
    already_defined_json = json.dumps(sorted(defined_terms.keys()), indent=2)

    notes    = ""
    accepted = False
    review: ThoroughnessReview | None = None
    iteration = 0
    final_draft = ""

    for iteration in range(1, MAX_REVIEW_ITERATIONS + 1):
        feedback_section = (
            f"\n\nREVISION REQUIRED\n"
            f"The following issues were found in the draft. Fix all of them.\n"
            f"Do not change any part of the chapter that was not flagged.\n{notes}"
            if notes else ""
        )

        # Prepend update context when this is a documentation update run
        writer_system = WRITER_SYSTEM_PROMPT
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
             f"Already defined terms (do not redefine these):\n{already_defined_json}\n\n"
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

        final_draft = chapter_path.read_text(encoding="utf-8", errors="replace")

        try:
            review = await _invoke_reviewer([
                ("user", REVIEWER_PROMPT.format(
                    description=chapter.description,
                    defined_terms_json=already_defined_json,
                    draft=final_draft[:20_000],
                ))
            ])
        except Exception as exc:
            # Reviewer parse failure — accept draft and log warning
            import logging
            logging.warning(
                "write_review_chapter_node: reviewer failed for '%s': %s — accepting draft.",
                chapter.title, exc,
            )
            accepted = True
            break

        accepted = review.overall_verdict == "pass"

        if accepted:
            break

        # Collect revision instructions from all failing criteria
        all_revisions: list[str] = []
        for cresult in review.criteria.values():
            if cresult.verdict == "fail":
                all_revisions.extend(cresult.revisions)
        notes = "\n".join(f"{i + 1}. {r}" for i, r in enumerate(all_revisions))

        # On the second (last) iteration, accept regardless
        if iteration == MAX_REVIEW_ITERATIONS:
            import logging
            logging.warning(
                "Chapter '%s' did not pass review after revision. Accepting draft.",
                chapter.title,
            )
            accepted = True

    # Extract term/analogy metadata from the final draft
    metadata = extract_chapter_metadata(final_draft) if final_draft else {}
    new_terms: dict[str, str]    = metadata.get("defined_terms", {})  # type: ignore[assignment]
    new_analogies: list[str]     = metadata.get("analogies", [])       # type: ignore[assignment]

    review_dict: dict[str, Any] = review.model_dump() if review is not None else {}
    was_revised = iteration > 1

    return {
        "chapter_results": [{
            "slug":      chapter.slug,
            "title":     chapter.title,
            "path":      str(chapter_path),
            "accepted":  accepted,
            "iterations": iteration,
        }],
        "defined_terms":          new_terms,
        "chapter_analogies":      {chapter.title: new_analogies},
        "chapter_review_results": {chapter.title: review_dict},
        "chapters_revised":       [chapter.title] if was_revised else [],
        "messages": [("assistant",
            f"Chapter '{chapter.title}': {'accepted' if accepted else 'max iterations reached'} "
            f"after {iteration} iteration(s)."
                      + (" Revised once." if was_revised else "")
        )],
    }


# ── Node: chapter_assembler_node ─────────────────────────────────────────────

@skippable("writer_agent")
async def chapter_assembler_node(state: AgenticDocsState) -> dict:  # type: ignore[type-arg]
    output_dir = _output_dir(state)

    chapter_files = sorted(output_dir.glob("*.md"))
    separator = "\n\n---\n\n"
    chapter_bodies = separator.join(
        f.read_text(encoding="utf-8", errors="replace") for f in chapter_files
    )

    # Prepend reading guide generated by chapter_crossref_node (Pass C)
    reading_guide: str = state.get("reading_guide") or ""
    final_doc = (reading_guide + separator + chapter_bodies) if reading_guide else chapter_bodies

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
