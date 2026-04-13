from pathlib import Path

from gitingest import ingest

from src.state import DocSmithState


def github_agent_node(state: DocSmithState) -> dict:
    url = state.get("github_url")
    if not url:
        return {"scratchpad_files": []}

    summary, tree, content = ingest(url)

    combined = (
        f"# Repository Summary\n{summary}\n\n"
        f"# Directory Tree\n{tree}\n\n"
        f"# Source Files\n{content}"
    )

    path = Path(state["scratchpad_dir"]) / "github.md"
    path.write_text(combined, encoding="utf-8", errors="replace")

    return {"scratchpad_files": [str(path)]}
