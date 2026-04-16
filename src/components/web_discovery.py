import json

from langchain_tavily import TavilySearch

from src.core.settings import settings
from src.graph.resumption import skippable
from src.graph.scratchpad import write_scratchpad
from src.state import AgenticDocsState


def _url_relevance_bonus(url: str, package_name: str) -> float:
    """
    Return a bonus score for GitHub URLs that look like the canonical source repo.
    Higher bonus = more likely to be the real repo rather than a fork, doc page, or
    unrelated project that merely mentions the package.
    """
    import re
    name_slug = re.sub(r"[^a-z0-9]", "", package_name.lower())
    try:
        from urllib.parse import urlparse
        parts = urlparse(url).path.strip("/").split("/")  # ['owner', 'repo', ...]
    except Exception:
        return 0.0

    if len(parts) < 2:
        return 0.0

    repo_slug = re.sub(r"[^a-z0-9]", "", parts[1].lower())

    # Exact repo-slug match is the strongest signal
    if repo_slug == name_slug:
        return 2.0
    # Repo slug contains the package name (e.g. "aiogram-contrib")
    if name_slug in repo_slug:
        return 1.0
    # Owner contains the package name (e.g. github.com/aiogram/...)
    owner_slug = re.sub(r"[^a-z0-9]", "", parts[0].lower())
    if name_slug in owner_slug:
        return 0.5
    return 0.0


@skippable("web_discovery")
def web_discovery_node(state: AgenticDocsState) -> dict:
    name, lang = state["package_name"], state["language"]

    github_search = TavilySearch(
        max_results=5,
        include_domains=["github.com"],
        search_depth="advanced",
        tavily_api_key=settings.tavily_api_key,
    )

    # Keep the query minimal — just the package name plus language/ecosystem.
    # Tavily's include_domains already restricts to github.com; adding more
    # operators or prose (like "source repository") confuses the ranker and
    # crowds out the package name as the primary signal.
    ecosystem = state.get("ecosystem", "")
    query = f"{name} {lang} {ecosystem}".strip()
    results = github_search.invoke(query)

    raw = results.get("results", [])

    # Deduplicate and boost by URL structure
    seen: set[str] = set()
    scored: list[tuple[float, dict]] = []
    for r in raw:
        url = r.get("url", "")
        if url in seen:
            continue
        seen.add(url)
        tavily_score = r.get("score", 0.0)
        bonus = _url_relevance_bonus(url, name)
        scored.append((tavily_score + bonus, r))

    scored.sort(key=lambda x: x[0], reverse=True)
    ranked = [r for _, r in scored[:5]]

    write_scratchpad(state["thread_id"], "web_discovery", json.dumps(ranked, indent=2))
    return {"search_results": ranked}
