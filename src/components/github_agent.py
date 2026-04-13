from gitingest import ingest
from src.state import DocSmithState
from gitingest import ingest


def github_agent_node(state: DocSmithState) -> dict:
    url = state.get("github_url")
    if not url:
        return {"github_content": []}

    summary, tree, content = ingest(url, max_file_size=50_000)

    # Enforce token budget: summary + tree always, code content trimmed
    combined = f"# Repository Summary\n{summary}\n\n# Directory Tree\n{tree}"
    budget_remaining = 60_000 - len(combined)
    if budget_remaining > 0:
        combined += f"\n\n# Source Files\n{content[:budget_remaining]}"

    return {"github_content": [combined]}