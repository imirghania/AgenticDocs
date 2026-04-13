from pathlib import Path

import httpx
from langchain_community.document_loaders import RecursiveUrlLoader
from bs4 import BeautifulSoup

from src.state import DocSmithState


async def docs_scraper_node(state: DocSmithState) -> dict:
    url = state.get("docs_url")
    if not url:
        return {"scratchpad_files": []}

    content = None

    # Prefer machine-readable llms.txt endpoints (no truncation)
    for suffix in ["/llms-full.txt", "/llms.txt"]:
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(url.rstrip("/") + suffix, 
                                    timeout=15, 
                                    follow_redirects=True)
            if r.status_code == 200:
                content = r.text
                break
        except Exception:
            pass

    # Fallback: recursive HTML scrape (all pages, full text)
    if content is None:
        loader = RecursiveUrlLoader(
            url=url,
            max_depth=3,
            extractor=lambda x: BeautifulSoup(x, "html.parser").get_text(),
            prevent_outside=True,
        )
        docs = loader.load()
        content = "\n\n---\n\n".join(d.page_content for d in docs)

    path = Path(state["scratchpad_dir"]) / "scraper.md"
    path.write_text(content, encoding="utf-8", errors="replace")

    return {"scratchpad_files": [str(path)]}
