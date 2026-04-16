from pathlib import Path

from deepagents import create_deep_agent, FilesystemPermission
from deepagents.backends import FilesystemBackend
from langchain_tavily import TavilySearch

from src.core.llm import llm
from src.graph.resumption import skippable
from src.graph.scratchpad import write_scratchpad
from src.state import AgenticDocsState


_ENRICHMENT_PROMPT = """You are a documentation researcher filling gaps identified by a quality review.
For each gap provided:
1. Use the search tool to find relevant examples, tutorials, and API references.
2. Write your findings as separate .md files in the scratchpad directory.
   Name each file clearly, e.g. gap_api_coverage.md, gap_code_examples.md.
Be thorough — write everything you find, no truncation."""


@skippable("enrichment_agent")
async def enrichment_node(state: AgenticDocsState) -> dict:
    report = state.get("quality_report", {})
    # Support both DimensionScore objects and plain dicts (after deserialization)
    gaps = []
    for dim in report.values():
        if isinstance(dim, dict):
            gaps.extend(dim.get("gaps", []))
        else:
            gaps.extend(dim.gaps)

    if not gaps:
        return {"scratchpad_files": []}

    scratchpad_dir = state["scratchpad_dir"]

    agent = create_deep_agent(
        model=llm,
        tools=[TavilySearch(max_results=5, topic="general")],
        system_prompt=_ENRICHMENT_PROMPT,
        permissions=[FilesystemPermission(
            operations=["read", "write"],
            paths=[str(Path(scratchpad_dir).absolute())],
        )],
        backend=FilesystemBackend(),
    )

    await agent.ainvoke({"messages": [("user",
        f"Package: {state['package_name']} ({state['language']})\n"
        f"Scratchpad directory: {scratchpad_dir}\n"
        f"Gaps to fill:\n" + "\n".join(f"- {g}" for g in gaps[:8])
    )]})

    new_files = [str(p) for p in sorted(Path(scratchpad_dir).glob("gap_*.md"))]

    # Combine all gap files into a single enrichment scratchpad entry
    combined_parts = []
    for f in new_files:
        try:
            combined_parts.append(Path(f).read_text(encoding="utf-8", errors="replace"))
        except OSError:
            pass
    combined = "\n\n---\n\n".join(combined_parts)
    if combined:
        write_scratchpad(state["thread_id"], "enrichment_agent", combined)

    return {"scratchpad_files": new_files}
