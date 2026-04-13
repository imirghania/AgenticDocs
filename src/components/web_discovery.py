from langchain_tavily import TavilySearch
from src.state import DocSmithState


github_search = TavilySearch(max_results=5, include_domains=["github.com"], search_depth="advanced")


def web_discovery_node(state: DocSmithState) -> dict:
    name, lang = state["package_name"], state["language"]

    results = github_search.invoke(f"{name} {lang} github repository")

    # Deduplicate by URL, rank by relevance score
    seen, ranked = set(), []
    for r in sorted(results.get("results", []), key=lambda x: x.get("score", 0), reverse=True):
        if r["url"] not in seen:
            seen.add(r["url"])
            ranked.append(r)

    return {"search_results": ranked[:5]}
