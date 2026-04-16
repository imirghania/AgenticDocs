"""
Chapter cross-reference enrichment node.

After all chapters pass their write-review loop, this node runs four passes:

0. (Existing) LLM per chapter — inserts backward refs "(covered in *Chapter N – Title*)"
   and forward refs "(explained in *Chapter N – Title*)" near relevant concepts.

A. Transition sentences — a single LLM call generates a 2–4 sentence bridge paragraph
   for every consecutive chapter pair; each paragraph is appended to the end of chapter N.

B. Concept callback annotations — programmatic (regex-based) pass that inserts
   *(introduced in [Chapter Title](#anchor))* on the FIRST occurrence of a cross-chapter
   term, skipping code blocks and terms shorter than 4 characters.

C. Reading guide — a single LLM call produces "## How to read this documentation"
   (prepended to the final assembled document via state["reading_guide"]).

Decorated with @skippable("chapter_crossref") so it is skipped on resume
when 09_crossref_done.json already exists.
"""
import json
import logging
import re
from pathlib import Path
from typing import Any

from src.components.writer import _output_dir
from src.core.llm import llm
from src.graph.resumption import skippable
from src.graph.scratchpad import write_scratchpad
from src.graph.store import put_session_meta
from src.graph.store import store as global_store
from src.prompts.crossref import CROSSREF_SYSTEM_PROMPT, TRANSITION_SYSTEM_PROMPT, READING_GUIDE_SYSTEM_PROMPT
from src.state import AgenticDocsState


# Helpers
def build_concept_index(
    chapter_files: list[Path],
    chapter_plan: list[str],
) -> dict[str, str]:
    """
    Scan each chapter file's ### Key terms section to build a map of
    term (lowercase) → chapter_title where it was first defined.
    """
    index: dict[str, str] = {}
    for chfile, title in zip(chapter_files, chapter_plan):
        try:
            text = chfile.read_text(encoding="utf-8", errors="replace")
            kt = re.search(
                r"###\s+Key terms?\s*\n(.*?)(?=\n#{1,3}\s|\Z)",
                text,
                re.IGNORECASE | re.DOTALL,
            )
            if not kt:
                continue
            for line in kt.group(1).splitlines():
                m = re.match(r"\s*\*\*(.+?)\*\*\s*[—–-]+\s*", line)
                if m:
                    term = m.group(1).strip().lower()
                    if term not in index:
                        index[term] = title
        except Exception:
            pass
    return index


def _insert_transition(text: str, paragraph: str) -> str:
    """
    Insert the transition paragraph into chapter text before ### Key terms /
    ### See also, or at the end of the file if neither heading exists.
    Never inserts between a heading and its first paragraph.
    """
    marker = re.search(r"\n###\s+(Key terms?|See also)", text, re.IGNORECASE)
    if marker:
        pos = marker.start()
        return text[:pos] + "\n\n" + paragraph + text[pos:]
    return text.rstrip() + "\n\n" + paragraph + "\n"


def _build_code_ranges(text: str) -> list[tuple[int, int]]:
    """Return (start, end) character ranges that are inside fenced or inline code."""
    ranges: list[tuple[int, int]] = []
    for m in re.finditer(r"```.*?```|`[^`\n]+`", text, re.DOTALL):
        ranges.append((m.start(), m.end()))
    return ranges


def _in_code(pos: int, code_ranges: list[tuple[int, int]]) -> bool:
    return any(s <= pos < e for s, e in code_ranges)


def _insert_concept_callbacks(
    text: str,
    chapter_title: str,
    concept_index: dict[str, str],
) -> tuple[str, list[str]]:
    """
    For each term in concept_index that was defined in a DIFFERENT chapter,
    annotate the FIRST occurrence (outside code blocks) in this chapter with:
        *(introduced in [Title](#anchor))*

    Returns (modified_text, list_of_annotated_terms).  Never raises.
    """
    annotated: list[str] = []
    code_ranges = _build_code_ranges(text)

    # Sort longest terms first to avoid partial matches of shorter sub-terms
    sorted_terms = sorted(concept_index.items(), key=lambda x: len(x[0]), reverse=True)

    for term, from_chapter in sorted_terms:
        if from_chapter == chapter_title:
            continue  # defined in this chapter — skip
        if len(term) < 4:
            continue  # too short

        anchor = from_chapter.lower().replace(" ", "-")
        ref = f"*(introduced in [{from_chapter}](#{anchor}))*"
        pattern = re.compile(r"\b" + re.escape(term) + r"\b", re.IGNORECASE)

        match = pattern.search(text)
        if not match or _in_code(match.start(), code_ranges):
            continue

        # Insert after the sentence containing this match
        sentence_end = re.search(r"[.!?](?:\s|$)", text[match.end():])
        if sentence_end:
            insert_pos = match.end() + sentence_end.start() + 1
        else:
            insert_pos = match.end()

        ref_text = " " + ref
        text = text[:insert_pos] + ref_text + text[insert_pos:]

        # Shift code ranges past the insertion point
        insert_len = len(ref_text)
        code_ranges = [
            (s, e) if e <= insert_pos else (s + insert_len, e + insert_len)
            for s, e in code_ranges
        ]
        annotated.append(term)

    return text, annotated


async def _generate_transitions(
    chapter_files: list[Path],
    chapter_plan: list[str],
) -> dict[str, str]:
    """
    Single LLM call for all consecutive chapter pairs.
    Returns {from_chapter_title: transition_paragraph}.
    If JSON parsing fails for any pair, that pair is silently skipped.
    Never raises.
    """
    if len(chapter_files) < 2:
        return {}

    pairs_context = ""
    for i in range(len(chapter_files) - 1):
        try:
            tail = chapter_files[i].read_text(encoding="utf-8", errors="replace")[-400:]
            head = chapter_files[i + 1].read_text(encoding="utf-8", errors="replace")[:400]
        except Exception:
            continue
        title_n   = chapter_plan[i]     if i     < len(chapter_plan) else chapter_files[i].stem
        title_n1  = chapter_plan[i + 1] if i + 1 < len(chapter_plan) else chapter_files[i + 1].stem
        pairs_context += (
            f"\n---\n"
            f"Chapter N title: {title_n}\n"
            f"Chapter N last 400 chars:\n{tail}\n"
            f"Chapter N+1 title: {title_n1}\n"
            f"Chapter N+1 first 400 chars:\n{head}\n"
        )

    if not pairs_context:
        return {}

    try:
        response = await llm.ainvoke([
            ("system", TRANSITION_SYSTEM_PROMPT),
            ("user", pairs_context),
        ])
        raw: str = response.content if hasattr(response, "content") else str(response)  # type: ignore[union-attr]
        pairs: Any = json.loads(raw)
        transitions: dict[str, str] = {}
        for p in pairs:
            if isinstance(p, dict) and "from_chapter" in p and "transition" in p:
                transitions[str(p["from_chapter"])] = str(p["transition"])
        return transitions
    except Exception as exc:
        logging.warning("_generate_transitions: LLM or JSON parse failed: %s", exc)
        return {}


async def _generate_reading_guide(
    chapter_files: list[Path],
    chapter_plan: list[str],
    defined_terms: dict[str, str],
) -> str:
    """
    Single LLM call to produce the ## How to read this documentation section.
    Returns empty string on failure.
    """
    first_sentences: list[str] = []
    for f in chapter_files:
        try:
            lines = [
                ln for ln in f.read_text(encoding="utf-8", errors="replace").splitlines()
                if ln.strip() and not ln.startswith("#")
            ]
            first_sentences.append(lines[0] if lines else "")
        except Exception:
            first_sentences.append("")

    user_msg = (
        "Chapter list:\n"
        + "\n".join(f"{i + 1}. {t}" for i, t in enumerate(chapter_plan))
        + "\n\nFirst sentence of each chapter:\n"
        + "\n".join(f"{i + 1}. {s}" for i, s in enumerate(first_sentences))
        + "\n\nKey terms defined across all chapters:\n"
        + "\n".join(f"- {k}" for k in list(defined_terms.keys())[:50])
    )

    try:
        response = await llm.ainvoke([
            ("system", READING_GUIDE_SYSTEM_PROMPT),
            ("user", user_msg),
        ])
        return response.content if hasattr(response, "content") else str(response)  # type: ignore[union-attr,return-value]
    except Exception as exc:
        logging.warning("_generate_reading_guide: LLM failed: %s", exc)
        return ""


# Node
@skippable("chapter_crossref")
async def chapter_crossref_node(state: AgenticDocsState) -> dict:  # type: ignore[type-arg]
    output_dir    = _output_dir(state)
    thread_id     = state["thread_id"]
    chapter_plan: list[str] = state.get("chapter_plan") or []

    chapter_files = sorted(output_dir.glob("*.md"))
    if not chapter_files:
        return {}

    # Pass 0 (existing): LLM per chapter — backward/forward refs
    all_chapters_text = "\n\n===CHAPTER BREAK===\n\n".join(
        f"**{f.stem}**\n"
        + f.read_text(encoding="utf-8", errors="replace")[:4_000]
        for f in chapter_files
    )
    chapter_list_str = "\n".join(
        f"{i + 1}. {title}" for i, title in enumerate(chapter_plan)
    )
    enriched_paths: list[str] = []
    for i, chapter_file in enumerate(chapter_files):
        draft = chapter_file.read_text(encoding="utf-8", errors="replace")
        try:
            response = await llm.ainvoke([
                ("system", CROSSREF_SYSTEM_PROMPT),
                ("user",
                f"Chapter list (in order):\n{chapter_list_str}\n\n"
                f"All chapters (for context, truncated):\n{all_chapters_text[:20_000]}\n\n"
                f"Enrich THIS chapter (chapter {i + 1}: {chapter_file.stem}):\n\n{draft}"
                ),
            ])
            enriched: str = (
                response.content  # type: ignore[union-attr]
                if hasattr(response, "content")
                else str(response)
            )
            chapter_file.write_text(enriched, encoding="utf-8")
            enriched_paths.append(str(chapter_file))
        except Exception as exc:
            logging.warning("chapter_crossref Pass 0 failed for %s: %s", chapter_file.name, exc)
            enriched_paths.append(str(chapter_file))

    # Reload files after Pass 0 modifications
    chapter_files = sorted(output_dir.glob("*.md"))

    # Pass A: Transition sentences (single LLM call)
    transitions = await _generate_transitions(chapter_files, chapter_plan)
    for i, chapter_file in enumerate(chapter_files[:-1]):   # skip last chapter
        title_n = chapter_plan[i] if i < len(chapter_plan) else chapter_file.stem
        para = transitions.get(title_n, "")
        if not para:
            continue
        try:
            text = chapter_file.read_text(encoding="utf-8", errors="replace")
            chapter_file.write_text(_insert_transition(text, para), encoding="utf-8")
        except Exception as exc:
            logging.warning("chapter_crossref Pass A failed for %s: %s", chapter_file.name, exc)

    # Reload after Pass A
    chapter_files = sorted(output_dir.glob("*.md"))

    # Pass B: Concept callback annotations (regex, no LLM)
    concept_index = build_concept_index(chapter_files, chapter_plan)
    callbacks_inserted: dict[str, list[str]] = {}
    for i, chapter_file in enumerate(chapter_files):
        title = chapter_plan[i] if i < len(chapter_plan) else chapter_file.stem
        try:
            text = chapter_file.read_text(encoding="utf-8", errors="replace")
            modified, annotated = _insert_concept_callbacks(text, title, concept_index)
            if annotated:
                chapter_file.write_text(modified, encoding="utf-8")
                callbacks_inserted[title] = annotated
        except Exception as exc:
            logging.warning("chapter_crossref Pass B failed for %s: %s", chapter_file.name, exc)

    # Reload after Pass B
    chapter_files = sorted(output_dir.glob("*.md"))

    # Pass C: Reading guide (single LLM call)
    defined_terms: dict[str, str] = dict(state.get("defined_terms") or {})
    reading_guide = await _generate_reading_guide(chapter_files, chapter_plan, defined_terms)

    # Scratchpad
    scratchpad_payload = {
        "concept_index":      concept_index,
        "chapter_transitions": transitions,
        "callbacks_inserted": callbacks_inserted,
        "reading_guide_word_count": len(reading_guide.split()) if reading_guide else 0,
        "chapters_enriched": enriched_paths,
    }
    write_scratchpad(
        thread_id,
        "chapter_crossref",
        json.dumps(scratchpad_payload, indent=2),
    )
    put_session_meta(global_store, thread_id, {"last_completed_node": "chapter_crossref"})

    return {
        "concept_index":      concept_index,
        "chapter_transitions": transitions,
        "reading_guide":      reading_guide,
        "messages": [("assistant",
            f"Cross-reference pass complete: {len(enriched_paths)} chapter(s) enriched, "
            f"{sum(len(v) for v in callbacks_inserted.values())} concept callbacks inserted, "
            f"reading guide {'generated' if reading_guide else 'skipped (LLM failed)'}."
        )],
    }
