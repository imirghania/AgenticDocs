from gitingest import ingest

from src.graph.resumption import skippable
from src.graph.scratchpad import write_scratchpad
from src.state import AgenticDocsState


@skippable("github_agent")
def github_agent_node(state: AgenticDocsState) -> dict:
    url = state.get("github_url")
    if not url:
        return {"scratchpad_files": []}

    summary, tree, content = ingest(url)

    combined = (
        f"# Repository Summary\n{summary}\n\n"
        f"# Directory Tree\n{tree}\n\n"
        f"# Source Files\n{content}"
    )

    written_path = write_scratchpad(state["thread_id"], "github_agent", combined)
    return {"scratchpad_files": [str(written_path)]}
