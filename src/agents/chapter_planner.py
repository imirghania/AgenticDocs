"""
Chapter planner agent node.

Determines the best chapter structure for a package's documentation by
analysing the aggregated source material via the LLM.

Key behaviours:
- Always uses the LLM — no static fallback chapter list.
- Retries once with an explicit "return only JSON" prompt if parsing fails.
- Raises ValueError after two failures rather than substituting a generic list.
- Writes 08_chapter_plan.json to the scratchpad for resumption.
- Updates the long-term session store with the chapter titles.
- Decorated with @skippable("chapter_planner") so it is skipped on resume
  when the scratchpad file already exists.
"""
import json

from src.components.writer import (
    ChapterPlan,
    ChapterSpec,  # re-exported for other modules
    _planner,
    _PLANNER_SYSTEM_PROMPT,
    _read_scratchpad_summary,
)
from src.graph.resumption import skippable
from src.graph.scratchpad import write_scratchpad
from src.graph.store import put_session_meta, store as global_store
from src.state import AgenticDocsState


@skippable("chapter_planner")
async def chapter_planner_node(state: AgenticDocsState) -> dict:
    summary = _read_scratchpad_summary(state["scratchpad_dir"])
    thread_id = state["thread_id"]
    user_msg = (
        f"Package: {state['package_name']} ({state['language']}, {state['ecosystem']})\n\n"
        f"Quality report:\n{state.get('quality_report', {})}\n\n"
        f"Source material summary:\n{summary}"
    )

    plan: ChapterPlan | None = None
    for attempt, extra in enumerate(["", "\n\nIMPORTANT: Return ONLY valid JSON. No prose."]):
        try:
            plan = await _planner.ainvoke([
                ("system", _PLANNER_SYSTEM_PROMPT + extra),
                ("user", user_msg),
            ])
            break
        except Exception:
            if attempt == 1:
                raise ValueError(
                    "chapter_planner_node: LLM failed to produce a valid ChapterPlan "
                    "after 2 attempts. Check the model and prompt."
                )

    assert plan is not None  # satisfies type checker after the loop

    chapters_as_dicts = [c.model_dump() for c in plan.chapters]
    chapter_plan = [c.title for c in plan.chapters]

    write_scratchpad(
        thread_id,
        "chapter_planner",
        json.dumps({"chapters": chapters_as_dicts, "chapter_plan": chapter_plan}, indent=2),
    )
    put_session_meta(global_store, thread_id, {
        "chapter_plan": chapter_plan,
        "last_completed_node": "chapter_planner",
    })

    return {
        "chapters": chapters_as_dicts,
        "chapter_plan": chapter_plan,
        "chapter_results": [],   # pre-initialize the accumulator before fan-out
        "messages": [("assistant",
            f"Chapter plan ({len(plan.chapters)} chapters): "
            + ", ".join(chapter_plan)
        )],
    }
