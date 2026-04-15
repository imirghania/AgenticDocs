"""
Step output formatter.

format_step_output() is the public entry point. It delegates to one
private _fmt_<node_name> function per node, keeping each node's
display logic isolated and easy to modify independently.

Called from the background graph thread — must not touch st.session_state.
"""

import json
from collections.abc import Callable
from pathlib import Path

from src.graph.scratchpad import read_scratchpad, SCRATCHPAD_FILES
from ui.constants import NODE_LABELS, NODE_ORDER


# ── Per-node formatters ────────────────────────────────────────────────────────
#
# Signature: (node_data: dict, _read_sp: Callable[[str], str | None]) -> dict
# Return:    {"summary": str, "details": list[str], **optional_extras}
#
# _read_sp(key) reads a scratchpad file for the current thread_id.
# node_data is {} for skipped (cached) nodes.

def _fmt_resumption_inspector(nd: dict, _read_sp: Callable) -> dict:
    is_resuming = nd.get("is_resuming", False)
    completed: set = nd.get("completed_nodes", set())

    if not is_resuming:
        return {"summary": "Fresh start", "details": []}

    # Nodes that have scratchpad files (and therefore can be skipped)
    skippable = [
        n for n in NODE_ORDER
        if n not in ("resumption_inspector", "aggregator", "intent_parser",
                     "docs_discovery", "write_review_chapter", "chapter_crossref")
    ]
    pending = [n for n in skippable if n not in completed]
    summary = f"Resuming — {len(completed)} nodes cached, {len(pending)} to run"

    rows = ["| Node | Status |", "|------|--------|"]
    for n in NODE_ORDER:
        if n == "resumption_inspector":
            continue
        label = NODE_LABELS.get(n, n)
        if n in completed:
            status = "✅ cached"
        elif n == "aggregator":
            status = "— (pass-through)"
        else:
            status = "▶ will run"
        rows.append(f"| {label} | {status} |")
    return {"summary": summary, "details": ["\n".join(rows)]}


def _fmt_intent_parser(nd: dict, _read_sp: Callable) -> dict:
    pkg  = nd.get("package_name", "")
    lang = nd.get("language", "")
    eco  = nd.get("ecosystem", "")
    if not pkg:
        return {"summary": "Request parsed", "details": []}
    summary = f"Identified: **{pkg}** ({lang}, {eco})"
    details = [json.dumps({"package_name": pkg, "language": lang, "ecosystem": eco}, indent=2)]
    return {"summary": summary, "details": details}


def _fmt_web_discovery(nd: dict, _read_sp: Callable) -> dict:
    results = nd.get("search_results", [])
    if not results:
        raw = _read_sp("web_discovery")
        if raw:
            try:
                results = json.loads(raw)
            except Exception:
                pass

    lines: list[str] = []
    for i, r in enumerate(results[:6], 1):
        title   = r.get("title", "")
        url     = r.get("url", "")
        snippet = (r.get("content", "") or r.get("snippet", ""))[:120].replace("\n", " ")
        lines.append(f"{i}. **{title}** — {url}")
        if snippet:
            lines.append(f"   _{snippet}_")
    return {
        "summary": f"Found {len(results)} results",
        "details": ["\n".join(lines)] if lines else [],
    }


def _fmt_confirm_package(nd: dict, _read_sp: Callable) -> dict:
    if not nd:
        raw = _read_sp("confirm_package")
        if raw:
            try:
                nd = json.loads(raw)
            except Exception:
                pass
    confirmed  = nd.get("confirmed_package") or {}
    github_url = nd.get("github_url", "")
    new_pkg    = nd.get("package_name", "")
    if confirmed:
        title   = confirmed.get("title", "")
        summary = f"User confirmed: **{title}** — {github_url or 'no GitHub URL'}"
    elif new_pkg:
        summary = f"Redirected to: **{new_pkg}**"
    else:
        summary = "Package confirmed"
    payload = {k: v for k, v in nd.items() if k != "messages"}
    return {"summary": summary, "details": [json.dumps(payload, indent=2, default=str)]}


def _fmt_docs_discovery(nd: dict, _read_sp: Callable) -> dict:
    docs_url = nd.get("docs_url", "")
    summary  = f"Found documentation: {docs_url}" if docs_url else "Documentation URL not found"
    return {"summary": summary, "details": []}


def _fmt_context7_agent(nd: dict, _read_sp: Callable) -> dict:
    content    = _read_sp("context7_agent") or ""
    char_count = len(content)
    sections   = content.count("\n## ") + (1 if content.startswith("## ") else 0)
    summary    = f"Retrieved {max(sections, 1)} documentation section(s) from Context7"
    details    = [f"{content[:800]}\n… ({char_count:,} chars total)"] if content else []
    return {"summary": summary, "details": details}


def _fmt_docs_scraper(nd: dict, _read_sp: Callable) -> dict:
    content    = _read_sp("docs_scraper") or ""
    char_count = len(content)
    details    = [f"{content[:600]}\n… ({char_count:,} chars total)"] if content else []
    return {"summary": f"Scraped documentation — {char_count:,} chars", "details": details}


def _fmt_github_agent(nd: dict, _read_sp: Callable) -> dict:
    content    = _read_sp("github_agent") or ""
    char_count = len(content)
    details: list[str] = []
    if "# Directory Tree" in content:
        try:
            tree_text  = content.split("# Directory Tree\n", 1)[1]
            tree_text  = tree_text.split("\n# ", 1)[0]
            tree_lines = tree_text.split("\n")
            total      = len(tree_lines)
            preview    = "\n".join(tree_lines[:40])
            if total > 40:
                preview += f"\n… ({total} lines total)"
            details = [preview]
        except Exception:
            pass
    return {"summary": f"Ingested repository — {char_count:,} chars", "details": details}


def _fmt_aggregator(nd: dict, _read_sp: Callable) -> dict:
    return {"summary": "Content aggregated — proceeding to quality evaluation", "details": []}


def _fmt_quality_judge(nd: dict, _read_sp: Callable) -> dict:
    quality_score  = nd.get("quality_score", 0)
    quality_report = nd.get("quality_report", {})
    if not quality_report:
        raw = _read_sp("quality_judge")
        if raw:
            try:
                data           = json.loads(raw)
                quality_score  = data.get("quality_score", 0)
                quality_report = data.get("quality_report", {})
            except Exception:
                pass

    overall          = quality_score * 5.0
    needs_enrichment = any(
        (v.get("score", 0) if isinstance(v, dict) else getattr(v, "score", 0)) <= 2
        for v in quality_report.values()
    )
    status_str = "Enrichment needed" if needs_enrichment else "Proceeding to writing"
    summary    = f"Quality score: **{overall:.1f} / 5.0** — {status_str}"

    rows = ["| Dimension | Score | Top gap |", "|-----------|-------|---------|"]
    for dim, dim_data in quality_report.items():
        score = (dim_data.get("score", 0) if isinstance(dim_data, dict)
                 else getattr(dim_data, "score", 0))
        gaps  = (dim_data.get("gaps", []) if isinstance(dim_data, dict)
                 else getattr(dim_data, "gaps", []))
        top_gap   = gaps[0][:60] if gaps else "—"
        dim_label = dim.replace("_", " ").title()
        if score <= 2:
            score_str = f'<span style="color:#E24B4A">▼ {score:.1f}</span>'
        elif score >= 4:
            score_str = f'<span style="color:#1D9E75">▲ {score:.1f}</span>'
        else:
            score_str = f"{score:.1f}"
        rows.append(f"| {dim_label} | {score_str} | {top_gap} |")
    return {"summary": summary, "details": ["\n".join(rows)]}


def _fmt_enrichment_agent(nd: dict, _read_sp: Callable) -> dict:
    files   = nd.get("scratchpad_files", [])
    n       = len([f for f in files if "gap_" in Path(f).name])
    summary = f"Enrichment complete — {n} additional source(s) collected"
    details = ["\n".join(f"- `{Path(f).name}`" for f in files)] if files else []
    return {"summary": summary, "details": details}


def _fmt_chapter_planner(nd: dict, _read_sp: Callable) -> dict:
    chapters = nd.get("chapter_plan", [])
    if not chapters:
        raw = _read_sp("chapter_planner")
        if raw:
            try:
                chapters = json.loads(raw).get("chapter_plan", [])
            except Exception:
                pass
    summary = f"Planned **{len(chapters)}** chapters"
    details = ["\n".join(f"{i+1}. {t}" for i, t in enumerate(chapters))] if chapters else []
    return {"summary": summary, "details": details, "_chapter_plan": chapters}


def _fmt_write_review_chapter(nd: dict, _read_sp: Callable) -> dict:
    results = nd.get("chapter_results", [])
    if results:
        r        = results[-1]
        title    = r.get("title", "")
        accepted = r.get("accepted", False)
        iters    = r.get("iterations", 1)
        mark     = "accepted ✓" if accepted else "needs revision"
        summary  = f"Chapter '{title}': {mark} after {iters} iteration(s)"
    else:
        summary = "Chapter written"
    return {
        "summary": summary,
        "details": [],
        "_chapter_result": results[-1] if results else {},
    }


def _fmt_chapter_crossref(nd: dict, _read_sp: Callable) -> dict:
    return {"summary": "Cross-reference pass complete — chapters enriched", "details": []}


def _fmt_chapter_assembler(nd: dict, _read_sp: Callable) -> dict:
    final_doc  = nd.get("final_documentation", "")
    output_dir = nd.get("output_file", "")
    word_count = len(final_doc.split()) if final_doc else 0
    chap_count = 0
    if output_dir:
        out = Path(output_dir)
        if out.is_dir():
            chap_count = len(list(out.glob("*.md")))
    summary = f"Documentation complete — **{word_count:,}** words, **{chap_count}** chapters"
    return {"summary": summary, "details": []}


# ── Dispatch table ─────────────────────────────────────────────────────────────

_FORMATTERS: dict[str, Callable] = {
    "resumption_inspector": _fmt_resumption_inspector,
    "intent_parser":        _fmt_intent_parser,
    "web_discovery":        _fmt_web_discovery,
    "confirm_package":      _fmt_confirm_package,
    "docs_discovery":       _fmt_docs_discovery,
    "context7_agent":       _fmt_context7_agent,
    "docs_scraper":         _fmt_docs_scraper,
    "github_agent":         _fmt_github_agent,
    "aggregator":           _fmt_aggregator,
    "quality_judge":        _fmt_quality_judge,
    "enrichment_agent":     _fmt_enrichment_agent,
    "chapter_planner":      _fmt_chapter_planner,
    "write_review_chapter": _fmt_write_review_chapter,
    "chapter_crossref":     _fmt_chapter_crossref,
    "chapter_assembler":    _fmt_chapter_assembler,
}


def format_step_output(node_name: str, node_data: dict, thread_id: str = "") -> dict:
    """
    Format a completed node's data for display in the pipeline step list.

    Returns {"summary": str, "details": list[str]} plus any optional extras
    (e.g. _chapter_plan, _chapter_result) used by event_processor.

    Safe to call from the background graph thread.
    """
    def _read_sp(key: str) -> str | None:
        return read_scratchpad(thread_id, key) if thread_id else None

    formatter = _FORMATTERS.get(node_name)
    if formatter:
        return formatter(node_data, _read_sp)
    return {"summary": f"{NODE_LABELS.get(node_name, node_name)} complete", "details": []}
