"""
Scratchpad file convention for DocSmith.

Every node that produces durable output writes a numbered file under
sessions/{thread_id}/ before returning. Presence of a non-empty file
is an unambiguous signal that its node completed successfully.
"""
import logging
import shutil
from pathlib import Path

SCRATCHPAD_FILES: dict[str, str] = {
    "local_cache_inspector": "00_cache_decision.json",
    "web_discovery":    "01_search_results.json",
    "confirm_package":  "02_confirmed_pkg.json",
    "context7_agent":   "03_context7_docs.md",
    "docs_scraper":     "04_scraped_docs.md",
    "github_agent":     "05_github_content.md",
    "quality_judge":    "06_quality_report.json",
    "enrichment_agent": "07_enrichment.md",
    "chapter_planner":  "08_chapter_plan.json",
    "chapter_crossref": "09_crossref_done.json",
    "writer_agent":     "10_final_output.md",
}


def _session_dir(thread_id: str) -> Path:
    """Returns Path('sessions/{thread_id}'), creating it if needed."""
    p = Path("sessions") / thread_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def write_scratchpad(thread_id: str, node_name: str, content: str) -> Path:
    """
    Write content to sessions/{thread_id}/{filename}.

    Idempotent: if the file already exists and has non-zero content,
    it is NOT overwritten. Logs a warning and returns the existing path.
    Raises KeyError if node_name is not in SCRATCHPAD_FILES.
    """
    filename = SCRATCHPAD_FILES[node_name]
    path = _session_dir(thread_id) / filename
    if path.exists() and path.read_text(encoding="utf-8").strip():
        logging.warning(
            "write_scratchpad: %s already exists and is non-empty — skipping overwrite",
            path,
        )
        return path
    path.write_text(content, encoding="utf-8")
    return path


def read_scratchpad(thread_id: str, node_name: str) -> str | None:
    """
    Return file content as a string, or None if the file doesn't exist or is empty.
    """
    filename = SCRATCHPAD_FILES.get(node_name)
    if not filename:
        return None
    path = Path("sessions") / thread_id / filename
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8").strip()
    return text if text else None


def list_completed_nodes(thread_id: str) -> set[str]:
    """
    Return the set of node names whose scratchpad file exists and is non-empty
    under sessions/{thread_id}/.
    """
    result: set[str] = set()
    base = Path("sessions") / thread_id
    for node_name, filename in SCRATCHPAD_FILES.items():
        path = base / filename
        try:
            if path.exists() and path.read_text(encoding="utf-8").strip():
                result.add(node_name)
        except OSError:
            pass
    return result


def read_scratchpad_summary(scratchpad_dir: str, max_chars: int = 12_000) -> str:
    """
    Read all .md files from a scratchpad directory, concatenate them
    (up to max_chars total), and return the combined string.
    Used by chapter_planner and quality_judge to build context.
    """
    import glob as glob_mod
    files = sorted(glob_mod.glob(f"{scratchpad_dir}/*.md"))
    parts = []
    for f in files:
        try:
            content = Path(f).read_text(encoding="utf-8", errors="replace")
            parts.append(f"## {Path(f).name}\n{content[:3_000]}")
        except OSError:
            pass
    combined = "\n\n---\n\n".join(parts)
    return combined[:max_chars]


def copy_scratchpad_from(
    source_thread_id: str,
    dest_thread_id: str,
    node_name: str,
) -> bool:
    """
    Copy the scratchpad file for node_name from source to dest session dir.

    Never overwrites a non-empty destination file (idempotent).
    Returns True if the file was copied or already existed in dest.
    Returns False if the source file is missing or empty.
    Uses shutil.copy2 to preserve mtime.
    """
    filename = SCRATCHPAD_FILES.get(node_name)
    if not filename:
        return False
    src = Path("sessions") / source_thread_id / filename
    try:
        if not src.exists() or not src.read_text(encoding="utf-8").strip():
            return False
    except OSError:
        return False
    dst = _session_dir(dest_thread_id) / filename
    try:
        if dst.exists() and dst.read_text(encoding="utf-8").strip():
            return True  # already present — idempotent
    except OSError:
        pass
    shutil.copy2(src, dst)
    return True
