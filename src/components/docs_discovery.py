import re

import httpx
from langchain_tavily import TavilySearch
from src.state import DocSmithState


_docs_search = TavilySearch(
    max_results=3,
    include_domains=["readthedocs.io", "docs.rs", "jsr.io"],
    search_depth="advanced",
)
_general_search = TavilySearch(max_results=3, topic="general")

_GITHUB_REPO_RE = re.compile(r"github\.com/([^/]+/[^/?#]+)")


def _repo_path(github_url: str) -> str | None:
    """Extract 'owner/repo' from a GitHub URL."""
    m = _GITHUB_REPO_RE.search(github_url)
    return m.group(1).rstrip(".git") if m else None


def _homepage_from_github_api(repo_path: str) -> str | None:
    """Call the GitHub API to get the repo's homepage field (no auth required for public repos)."""
    try:
        resp = httpx.get(
            f"https://api.github.com/repos/{repo_path}",
            headers={
                "Accept": "application/vnd.github.v3+json", "User-Agent": "docsmith-agent"
                },
            timeout=10,
            follow_redirects=True,
        )
        resp.raise_for_status()
        homepage = (resp.json().get("homepage") or "").strip()
        
        return (
            homepage 
            if homepage.startswith("http") 
            else None
            )
    except (httpx.HTTPError, KeyError, ValueError):
        return None


def _first_result_url(tavily_response: dict) -> str | None:
    results = tavily_response.get("results", [])
    return results[0].get("url") if results else None


def docs_discovery_node(state: DocSmithState) -> dict:
    github_url = state.get("github_url")
    package_name = state["package_name"]
    language = state["language"]

    docs_url = None

    # Step 1: Look for the docs URL in the GitHub repo's metadata
    if github_url:
        repo_path = _repo_path(github_url)
        if repo_path:
            docs_url = _homepage_from_github_api(repo_path)

    # Step 2: Try known documentation hosting sites
    if not docs_url:
        response = _docs_search.invoke(f"{package_name} {language} documentation")
        docs_url = _first_result_url(response)

    # Step 3: Broad web fallback
    if not docs_url:
        response = _general_search.invoke(f"{package_name} {language} official documentation")
        docs_url = _first_result_url(response)

    return {"docs_url": docs_url}
