from langchain_tavily import TavilySearch
from gitingest import ingest
from src.state import DocSmithState

_general_search = TavilySearch(max_results=3, topic="general")


def enrichment_node(state: DocSmithState) -> dict:
    report = state.get("quality_report", {})
    gaps = []
    for dim, score in report.items():
        gaps.extend(score.gaps)

    # Targeted follow-up searches based on identified gaps
    extra_context: list[str] = []
    for gap in gaps[:5]:
        result = _general_search.invoke(
            f"{state['package_name']} {gap} example tutorial"
        )
        extra_context.extend(r["content"] for r in result.get("results", []))

    # Also dig into /examples and /tests directories in the GitHub repo
    if state.get("github_url"):
        _, _, examples_content = ingest(
            state["github_url"],
            include_patterns=["examples/**", "tests/**", "*.md"]
        )
        extra_context.append(examples_content[:20_000])

    return {
        "github_content": extra_context,   # appended via operator.add reducer
    }