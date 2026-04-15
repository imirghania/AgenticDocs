"""
Chapter cross-reference enrichment node.

After all chapters pass their write-review loop, this node:
1. Reads every accepted chapter .md file from output/{package_slug}/.
2. For each chapter, calls the LLM to insert backward refs
   ("covered in *Chapter N – Title*") and forward refs
   ("explained in *Chapter N – Title*").
3. Writes the enriched content back to the same file in-place.
4. Writes a completion marker to the scratchpad.

Decorated with @skippable("chapter_crossref") so it is skipped on resume
when 09_crossref_done.json already exists.
"""
import json
from pathlib import Path

from src.components.writer import _output_dir
from src.core.llm import llm
from src.graph.resumption import skippable
from src.graph.scratchpad import write_scratchpad
from src.graph.store import put_session_meta, store as global_store
from src.state import DocSmithState


_CROSSREF_SYSTEM_PROMPT = """\
You are a technical documentation editor specialising in cross-chapter coherence.
You will receive the full text of all chapters in a documentation set, along with
the ordered chapter list.

For the SINGLE chapter you are asked to enrich:
- Add a short inline parenthetical near any concept that was introduced in a
  PREVIOUS chapter. Format: "(covered in *Chapter N – Title*)"
- Add a short forward-reference sentence near any concept that will be explained
  in a LATER chapter. Format: "(explained in *Chapter N – Title*)"
- Do NOT add a reference if the concept is fully self-contained within the
  current chapter.
- Do NOT alter code examples, headings, or the overall structure.
- Return ONLY the full enriched chapter text, nothing else.\
"""


@skippable("chapter_crossref")
async def chapter_crossref_node(state: DocSmithState) -> dict:
    output_dir = _output_dir(state)
    thread_id = state["thread_id"]
    chapter_plan: list[str] = state.get("chapter_plan", [])

    chapter_files = sorted(output_dir.glob("*.md"))
    if not chapter_files:
        # No chapters written yet — skip gracefully
        return {}

    # Build condensed context: first 4 000 chars of each chapter
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

        response = await llm.ainvoke([
            ("system", _CROSSREF_SYSTEM_PROMPT),
            ("user",
             f"Chapter list (in order):\n{chapter_list_str}\n\n"
             f"All chapters (for context, truncated):\n{all_chapters_text[:20_000]}\n\n"
             f"Enrich THIS chapter (chapter {i + 1}: {chapter_file.stem}):\n\n{draft}"
             ),
        ])

        enriched: str = (
            response.content
            if hasattr(response, "content")
            else str(response)
        )
        chapter_file.write_text(enriched, encoding="utf-8")
        enriched_paths.append(str(chapter_file))

    write_scratchpad(
        thread_id,
        "chapter_crossref",
        json.dumps({"chapters_enriched": enriched_paths}, indent=2),
    )
    put_session_meta(global_store, thread_id, {"last_completed_node": "chapter_crossref"})

    return {
        "messages": [("assistant",
            f"Cross-reference pass complete: enriched {len(enriched_paths)} chapter(s) "
            f"in `{output_dir}/`."
        )],
    }
