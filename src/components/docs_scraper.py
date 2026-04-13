import httpx
from langchain_community.document_loaders import RecursiveUrlLoader
from bs4 import BeautifulSoup
from src.state import DocSmithState


async def docs_scraper_node(state: DocSmithState) -> dict:
    url = state.get("docs_url")
    if not url:
        return {"scraped_docs": []}

    # Prioritize llms.txt if available
    for suffix in ["/llms-full.txt", "/llms.txt"]:
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(url.rstrip("/") + suffix, timeout=10)
            if r.status_code == 200:
                return {"scraped_docs": [r.text[:80_000]]}  # token budget
        except Exception:
            pass

    # Fallback: scrape the docs site (max 20 pages, filter to relevant content)
    loader = RecursiveUrlLoader(
        url=url, max_depth=2, extractor=lambda x: BeautifulSoup(x, "html.parser").get_text(),
        prevent_outside=True
    )
    docs = loader.load()
    content = "\n\n---\n\n".join(d.page_content[:3000] for d in docs[:20])
    return {"scraped_docs": [content]}