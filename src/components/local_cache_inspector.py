"""
local_cache_inspector_node — runs after confirm_package, before ingestion fan-out.

Detects three states:
  A — Completed documentation exists (10_final_output.md present):
      Offers View / Update (with change analysis) / Regenerate via HITL.
  B — Partial resources exist but no final doc:
      Offers Resume from partial cache / Regenerate via HITL.
  C — No local resources at all:
      Proceeds directly, no interrupt.

Uses interrupt() for HITL (same mechanism as confirm_package_node).
GitHub API calls use httpx with a 15-second timeout.
LLM update assessment is a direct llm.invoke call.
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from langgraph.types import interrupt

from src.core.llm import llm
from src.graph.resumption import skippable
from src.graph.scratchpad import (
    SCRATCHPAD_FILES,
    copy_scratchpad_from,
    read_scratchpad,
    write_scratchpad,
)
from src.graph.store import (
    find_matching_sessions,
    get_session_meta,
    put_session_meta,
    store as global_store,
)
from src.state import DocSmithState

_GITHUB_TIMEOUT = 15.0  # seconds per request

# Files 01–07 are the ingestion/evaluation outputs
_INGESTION_NODES = [
    "web_discovery",
    "confirm_package",
    "context7_agent",
    "docs_scraper",
    "github_agent",
    "quality_judge",
    "enrichment_agent",
]

_ASSESSMENT_SYSTEM_PROMPT = """\
You are a technical analyst assessing whether a software library has changed \
significantly since a documentation snapshot was taken.
Significant means: a major or minor version release, breaking API changes, \
important new features, or security fixes. Bug-fix-only releases and \
documentation-only changes are NOT significant.
Respond only with valid JSON matching this exact schema:
{
  "is_significant": bool,
  "significance_level": "major" | "minor" | "patch" | "none",
  "summary": str,
  "new_releases": [{"tag": str, "title": str, "highlights": str}],
  "breaking_changes": [str],
  "new_features": [str],
  "recommendation": "full_refresh" | "partial_refresh" | "no_update"
}
recommendation rules:
  full_refresh    — major release or 3+ breaking changes
  partial_refresh — minor release or new features without breakage
  no_update       — only patch/fix commits, no releases"""

# Which nodes to reuse (not re-run) per significance level.
# Nodes NOT in this list will be re-run fresh.
_REUSE_TABLE: dict[str, list[str]] = {
    "major": [],
    "minor": ["chapter_planner"],
    "patch": ["quality_judge", "chapter_planner"],
    "none":  ["quality_judge", "chapter_planner"],
}


# Main node
@skippable("local_cache_inspector")
async def local_cache_inspector_node(state: DocSmithState) -> dict:
    thread_id  = state["thread_id"]
    pkg_name   = state["package_name"]
    github_url = state.get("github_url") or ""

    # Persist github_url so future find_matching_sessions calls can match on it
    if github_url:
        put_session_meta(global_store, thread_id, {"github_url": github_url})

    # STEP 1 — find matching sessions for this package
    all_sessions = find_matching_sessions(global_store, pkg_name, github_url)
    # Exclude the current session (might show up if being resumed)
    all_sessions = [s for s in all_sessions if s.get("thread_id") != thread_id]

    completed_sessions = [
        s for s in all_sessions
        if s.get("status") == "completed" and _final_doc_exists(s["thread_id"])
    ]
    partial_sessions = [
        s for s in all_sessions
        if s not in completed_sessions and _has_ingestion_files(s["thread_id"])
    ]

    # STATE C — no cache at all
    if not completed_sessions and not partial_sessions:
        _write_decision(thread_id, "regenerate")
        return {"cache_decision": "regenerate"}

    # STATE A — completed documentation exists
    if completed_sessions:
        best = completed_sessions[0]
        resp: dict = interrupt({
            "type": "existing_doc_found",
            "data": _build_state_a_payload(pkg_name, github_url, completed_sessions),
        })
        decision   = resp.get("decision", "regenerate")
        source_tid = resp.get("source_thread_id") or best["thread_id"]

        if decision == "view":
            _write_decision(thread_id, "view", source_tid)
            return {
                "cache_decision":        "view",
                "cache_source_thread_id": source_tid,
            }

        if decision == "regenerate":
            _write_decision(thread_id, "regenerate")
            return {"cache_decision": "regenerate"}

        # decision == "update" → run change analysis
        baseline_date = best.get("updated_at", "")
        assessment    = await _run_update_check(github_url, pkg_name, baseline_date)

        resp2: dict = interrupt({
            "type": "update_assessment",
            "data": {
                "package_name":          pkg_name,
                "github_url":            github_url,
                "baseline_date":         baseline_date,
                "update_check_available": assessment.get("update_check_available", True),
                "assessment":            assessment,
                "source_thread_id":      source_tid,
            },
        })
        decision2        = resp2.get("decision", "cancel_update")
        refresh_strategy = resp2.get("refresh_strategy")

        if decision2 == "cancel_update":
            _write_decision(thread_id, "view", source_tid)
            return {
                "cache_decision":        "view",
                "cache_source_thread_id": source_tid,
            }

        # proceed_update
        prev_summary = _read_previous_summary(source_tid)
        significance = assessment.get("significance_level", "minor")

        if refresh_strategy == "full_refresh":
            _write_decision(thread_id, "full_refresh", source_tid, refresh_strategy, assessment)
            return {
                "cache_decision":        "full_refresh",
                "cache_source_thread_id": source_tid,
                "refresh_strategy":       "full_refresh",
                "is_update":              True,
                "previous_doc_summary":   prev_summary,
                "update_assessment":      assessment,
            }

        # partial_refresh — copy reuse candidates from source
        reuse_nodes = _REUSE_TABLE.get(significance, [])
        copied: list[str] = []
        for node in reuse_nodes:
            if copy_scratchpad_from(source_tid, thread_id, node):
                copied.append(node)

        _write_decision(thread_id, "partial_refresh", source_tid, refresh_strategy, assessment)
        return {
            "cache_decision":        "partial_refresh",
            "cache_source_thread_id": source_tid,
            "refresh_strategy":       "partial_refresh",
            "is_update":              True,
            "previous_doc_summary":   prev_summary,
            "update_assessment":      assessment,
            "completed_nodes":        set(copied),
        }

    # STATE B — partial resources only
    best_partial = partial_sessions[0]
    resp_b: dict = interrupt({
        "type": "partial_cache_found",
        "data": _build_state_b_payload(pkg_name, github_url, partial_sessions),
    })
    decision_b = resp_b.get("decision", "regenerate")
    source_tid_b = resp_b.get("source_thread_id") or best_partial["thread_id"]

    if decision_b == "regenerate":
        _write_decision(thread_id, "regenerate")
        return {"cache_decision": "regenerate"}

    # use_partial — copy completed nodes from source session
    completed_in_source: list[str] = best_partial.get("completed_nodes") or []
    if isinstance(completed_in_source, set):
        completed_in_source = list(completed_in_source)
    copied_b: list[str] = []
    for node in completed_in_source:
        if copy_scratchpad_from(source_tid_b, thread_id, node):
            copied_b.append(node)

    # Restore state fields from copied files (mirrors resumption_inspector)
    updates = _load_partial_state(source_tid_b, copied_b)
    updates.update({
        "cache_decision":        "use_partial",
        "cache_source_thread_id": source_tid_b,
        "completed_nodes":        set(copied_b),
    })
    _write_decision(thread_id, "use_partial", source_tid_b)
    return updates


# Helpers: session detection
def _final_doc_exists(thread_id: str) -> bool:
    """Return True if 10_final_output.md exists and is non-empty."""
    path = Path("sessions") / thread_id / SCRATCHPAD_FILES["writer_agent"]
    try:
        return path.exists() and bool(path.read_text(encoding="utf-8").strip())
    except OSError:
        return False


def _has_ingestion_files(thread_id: str) -> bool:
    """Return True if at least one ingestion file (01–07) exists and is non-empty."""
    base = Path("sessions") / thread_id
    for node in _INGESTION_NODES:
        filename = SCRATCHPAD_FILES.get(node, "")
        if not filename:
            continue
        path = base / filename
        try:
            if path.exists() and path.read_text(encoding="utf-8").strip():
                return True
        except OSError:
            pass
    return False


# Helpers: HITL payload builders
def _build_state_a_payload(
    pkg_name: str,
    github_url: str,
    completed_sessions: list[dict],
) -> dict:
    best = completed_sessions[0]
    # meta = get_session_meta(global_store, best["thread_id"]) or {}

    def _session_summary(s: dict) -> dict:
        m = get_session_meta(global_store, s["thread_id"]) or s
        return {
            "thread_id":     s["thread_id"],
            "updated_at":    s.get("updated_at", ""),
            "quality_score": m.get("quality_score"),
            "chapter_count": m.get("chapter_count"),
            "word_count":    m.get("word_count"),
        }

    return {
        "package_name": pkg_name,
        "github_url":   github_url,
        "best_match":   _session_summary(best),
        "other_matches": [_session_summary(s) for s in completed_sessions[1:]],
    }


def _build_state_b_payload(
    pkg_name: str,
    github_url: str,
    partial_sessions: list[dict],
) -> dict:
    best = partial_sessions[0]
    meta = get_session_meta(global_store, best["thread_id"]) or {}
    completed_nodes = best.get("completed_nodes") or []
    if isinstance(completed_nodes, set):
        completed_nodes = list(completed_nodes)
    return {
        "package_name": pkg_name,
        "github_url":   github_url,
        "best_partial": {
            "thread_id":           best["thread_id"],
            "updated_at":          best.get("updated_at", ""),
            "last_completed_node": meta.get("last_completed_node"),
            "completed_nodes":     completed_nodes,
        },
    }


# Helpers: GitHub change detection
def _extract_owner_repo(github_url: str) -> tuple[str, str] | None:
    """Parse 'https://github.com/owner/repo' → ('owner', 'repo')."""
    from urllib.parse import urlparse
    try:
        path_parts = urlparse(github_url).path.strip("/").split("/")
        if len(path_parts) >= 2:
            return path_parts[0], path_parts[1].removesuffix(".git")
    except Exception:
        pass
    return None


def _parse_utc(s: str) -> datetime | None:
    """Parse ISO-8601 string to timezone-aware UTC datetime; handles Z suffix."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


async def _run_update_check(
    github_url: str,
    pkg_name: str,
    baseline_date: str,
) -> dict:
    """
    Query GitHub API for changes since baseline_date, then ask the LLM
    to assess significance. Returns the assessment dict.
    On any failure, degrades gracefully with update_check_available=False.
    """
    fallback = {
        "is_significant": False,
        "significance_level": "none",
        "summary": "Could not assess changes automatically.",
        "new_releases": [],
        "breaking_changes": [],
        "new_features": [],
        "recommendation": "partial_refresh",
        "update_check_available": False,
    }

    owner_repo = _extract_owner_repo(github_url)
    if not owner_repo:
        return fallback
    owner, repo = owner_repo

    baseline_dt = _parse_utc(baseline_date)
    baseline_iso = baseline_dt.isoformat() if baseline_dt else baseline_date

    releases_data: list[dict] = []
    recent_commits: list[dict] = []
    commit_count = 0
    issues_data: list[dict] = []

    try:
        async with httpx.AsyncClient(timeout=_GITHUB_TIMEOUT) as client:
            # Releases
            r = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}/releases",
                params={"per_page": 10},
                headers={"Accept": "application/vnd.github+json"},
            )
            if r.status_code in (403, 429):
                return fallback
            if r.status_code == 200:
                for rel in r.json():
                    pub = _parse_utc(rel.get("published_at", ""))
                    if baseline_dt and pub and pub <= baseline_dt:
                        continue
                    releases_data.append({
                        "tag":          rel.get("tag_name", ""),
                        "title":        rel.get("name", ""),
                        "published_at": rel.get("published_at", ""),
                        "highlights":   (rel.get("body") or "")[:1000],
                    })

            # Commits
            r2 = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}/commits",
                params={"since": baseline_iso, "per_page": 100},
                headers={"Accept": "application/vnd.github+json"},
            )
            if r2.status_code in (403, 429):
                return fallback
            if r2.status_code == 200:
                commits = r2.json()
                commit_count = len(commits)
                for c in commits[:5]:
                    msg = (c.get("commit", {}).get("message") or "").split("\n")[0]
                    recent_commits.append({
                        "sha":    (c.get("sha") or "")[:7],
                        "message": msg,
                        "author":  c.get("commit", {}).get("author", {}).get("name", ""),
                        "date":    c.get("commit", {}).get("author", {}).get("date", ""),
                    })

            # Significant closed issues / PRs
            r3 = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}/issues",
                params={
                    "state": "closed",
                    "since": baseline_iso,
                    "labels": "breaking-change,major,enhancement",
                    "per_page": 20,
                },
                headers={"Accept": "application/vnd.github+json"},
            )
            if r3.status_code == 200:
                for issue in r3.json():
                    issues_data.append({
                        "number":    issue.get("number"),
                        "title":     issue.get("title", ""),
                        "closed_at": issue.get("closed_at", ""),
                        "labels":    [lb.get("name", "") for lb in issue.get("labels", [])],
                    })
    except Exception as exc:
        logging.warning("local_cache_inspector: GitHub API error: %s", exc)
        return fallback

    # LLM assessment
    user_msg = (
        f"Package: {pkg_name}\n"
        f"Baseline date: {baseline_date}\n"
        f"Releases since baseline: {json.dumps(releases_data)}\n"
        f"Recent commits ({commit_count} total, showing latest 5):\n"
        f"  {json.dumps(recent_commits)}\n"
        f"Significant closed issues/PRs: {json.dumps(issues_data)}"
    )

    for attempt, extra in enumerate(["", "\n\nIMPORTANT: Return ONLY valid JSON, no prose."]):
        try:
            response = await llm.ainvoke([
                ("system", _ASSESSMENT_SYSTEM_PROMPT + extra),
                ("user", user_msg),
            ])
            raw_text = response.content if hasattr(response, "content") else str(response)
            # Strip markdown code fences if present
            raw_text = raw_text.strip()
            if raw_text.startswith("```"):
                raw_text = raw_text.split("```")[1]
                if raw_text.startswith("json"):
                    raw_text = raw_text[4:]
            assessment = json.loads(raw_text.strip())
            assessment["update_check_available"] = True
            return assessment
        except Exception as exc:
            if attempt == 1:
                logging.warning("local_cache_inspector: LLM assessment failed: %s", exc)
                return fallback
    return fallback  # unreachable but satisfies type checker


# Helpers: state restoration for partial cache
def _load_partial_state(source_tid: str, copied_nodes: list[str]) -> dict:
    """
    Load state fields from copied scratchpad files.
    Mirrors the field-loading logic in resumption_inspector_node.
    """
    updates: dict[str, Any] = {}

    if "web_discovery" in copied_nodes:
        raw = read_scratchpad(source_tid, "web_discovery")
        if raw:
            try:
                updates["search_results"] = json.loads(raw)
            except json.JSONDecodeError:
                pass

    if "confirm_package" in copied_nodes:
        raw = read_scratchpad(source_tid, "confirm_package")
        if raw:
            try:
                data = json.loads(raw)
                updates["confirmed_package"] = data.get("confirmed_package")
                updates["github_url"]        = data.get("github_url")
                updates["docs_url"]          = data.get("docs_url")
            except json.JSONDecodeError:
                pass

    if "quality_judge" in copied_nodes:
        raw = read_scratchpad(source_tid, "quality_judge")
        if raw:
            try:
                data = json.loads(raw)
                updates["quality_score"]  = data.get("quality_score")
                updates["quality_report"] = data.get("quality_report")
            except json.JSONDecodeError:
                pass

    if "chapter_planner" in copied_nodes:
        raw = read_scratchpad(source_tid, "chapter_planner")
        if raw:
            try:
                data = json.loads(raw)
                updates["chapters"]     = data.get("chapters")
                updates["chapter_plan"] = data.get("chapter_plan")
            except json.JSONDecodeError:
                pass

    return updates


def _read_previous_summary(source_thread_id: str) -> str:
    """Read first 2000 characters of the source session's final output."""
    raw = read_scratchpad(source_thread_id, "writer_agent")
    return (raw or "")[:2000]


def _write_decision(
    thread_id: str,
    decision: str,
    source_thread_id: str | None = None,
    refresh_strategy: str | None = None,
    assessment: dict | None = None,
) -> None:
    """Persist cache decision to 00_cache_decision.json."""
    payload: dict[str, Any] = {"decision": decision}
    if source_thread_id:
        payload["source_thread_id"] = source_thread_id
    if refresh_strategy:
        payload["refresh_strategy"] = refresh_strategy
    if assessment:
        payload["assessment"] = assessment
    write_scratchpad(thread_id, "local_cache_inspector", json.dumps(payload, indent=2))
