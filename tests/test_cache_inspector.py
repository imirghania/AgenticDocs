"""
Tests for src/components/local_cache_inspector.py

All LLM calls, GitHub API calls, file I/O, and store operations are mocked.
interrupt() is mocked to return controlled HITL responses.
"""
import json
import pytest
import pytest_asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call


# Fixtures
@pytest.fixture
def base_state():
    return {
        "thread_id":       "test-cache-thread",
        "user_id":         "user-42",
        "package_name":    "requests",
        "language":        "Python",
        "ecosystem":       "PyPI",
        "github_url":      "https://github.com/psf/requests",
        "completed_nodes": set(),
        "scratchpad_files": [],
        "messages":        [("user", "document requests")],
    }


def _make_session(
    thread_id: str,
    status: str = "completed",
    completed_nodes: list | None = None,
    updated_at: str = "2025-01-01T00:00:00+00:00",
) -> dict:
    return {
        "thread_id":       thread_id,
        "status":          status,
        "package_name":    "requests",
        "github_url":      "https://github.com/psf/requests",
        "updated_at":      updated_at,
        "quality_score":   4.2,
        "chapter_count":   6,
        "word_count":      5000,
        "completed_nodes": completed_nodes or [],
    }


# Test 1: State C — no cache
@pytest.mark.asyncio
async def test_state_c_no_cache(base_state):
    """No matching sessions → no interrupt, cache_decision == 'regenerate'."""
    with (
        patch("src.components.local_cache_inspector.find_matching_sessions", return_value=[]),
        patch("src.components.local_cache_inspector.write_scratchpad") as mock_write,
    ):
        from src.components.local_cache_inspector import local_cache_inspector_node

        result = await local_cache_inspector_node(base_state)

    assert result["cache_decision"] == "regenerate"
    mock_write.assert_called_once()
    # Verify no interrupt was raised (would fail if interrupt() were called without mocking)


# Test 2: State A + "view"
@pytest.mark.asyncio
async def test_state_a_view(base_state):
    """Completed session found; user chooses 'view'. No ingestion nodes should run."""
    completed_session = _make_session("source-thread-001")

    with (
        patch("src.components.local_cache_inspector.find_matching_sessions",
              return_value=[completed_session]),
        patch("src.components.local_cache_inspector._final_doc_exists", return_value=True),
        patch("src.components.local_cache_inspector._has_ingestion_files", return_value=False),
        patch("src.components.local_cache_inspector.get_session_meta",
              return_value=completed_session),
        patch("src.components.local_cache_inspector.interrupt",
              return_value={"decision": "view", "source_thread_id": "source-thread-001"}),
        patch("src.components.local_cache_inspector.write_scratchpad"),
        patch("src.components.local_cache_inspector.put_session_meta"),
    ):
        from src.components.local_cache_inspector import local_cache_inspector_node

        result = await local_cache_inspector_node(base_state)

    assert result["cache_decision"] == "view"
    assert result["cache_source_thread_id"] == "source-thread-001"


# Test 3: State A + update + full_refresh
@pytest.mark.asyncio
async def test_state_a_update_full_refresh(base_state):
    """User chooses 'update'; update check runs; user chooses 'full_refresh'.
    Verify is_update=True and previous_doc_summary is populated."""
    completed_session = _make_session("source-thread-002")

    mock_assessment = {
        "is_significant":      True,
        "significance_level":  "major",
        "summary":             "Version 3.0 released with breaking changes.",
        "new_releases":        [{"tag": "v3.0.0", "title": "Major Release", "highlights": "..."}],
        "breaking_changes":    ["Removed HTTPAdapter.send()"],
        "new_features":        ["Async support"],
        "recommendation":      "full_refresh",
        "update_check_available": True,
    }

    interrupt_responses = iter([
        {"decision": "update",         "source_thread_id": "source-thread-002"},
        {"decision": "proceed_update", "refresh_strategy": "full_refresh"},
    ])

    with (
        patch("src.components.local_cache_inspector.find_matching_sessions",
            return_value=[completed_session]),
        patch("src.components.local_cache_inspector._final_doc_exists", return_value=True),
        patch("src.components.local_cache_inspector._has_ingestion_files", return_value=False),
        patch("src.components.local_cache_inspector.get_session_meta",
            return_value=completed_session),
        patch("src.components.local_cache_inspector.interrupt",
            side_effect=interrupt_responses),
        patch("src.components.local_cache_inspector._run_update_check",
            new_callable=AsyncMock, return_value=mock_assessment),
        patch("src.components.local_cache_inspector._read_previous_summary",
            return_value="Previous docs summary here."),
        patch("src.components.local_cache_inspector.write_scratchpad"),
        patch("src.components.local_cache_inspector.put_session_meta"),
    ):
        from src.components.local_cache_inspector import local_cache_inspector_node

        result = await local_cache_inspector_node(base_state)

    assert result["cache_decision"] == "full_refresh"
    assert result["is_update"] is True
    assert result["previous_doc_summary"] == "Previous docs summary here."
    assert result["refresh_strategy"] == "full_refresh"
    assert result["update_assessment"] == mock_assessment


# Test 4: State A + update + partial_refresh (minor significance)
@pytest.mark.asyncio
async def test_state_a_update_partial_refresh_minor(base_state):
    """Minor significance: context7/docs/github re-run, quality_judge re-run,
    chapter_planner reused (skipped via completed_nodes)."""
    completed_session = _make_session("source-thread-003")

    mock_assessment = {
        "is_significant":      True,
        "significance_level":  "minor",
        "summary":             "Minor feature additions.",
        "new_releases":        [],
        "breaking_changes":    [],
        "new_features":        ["New retry policy"],
        "recommendation":      "partial_refresh",
        "update_check_available": True,
    }

    interrupt_responses = iter([
        {"decision": "update",         "source_thread_id": "source-thread-003"},
        {"decision": "proceed_update", "refresh_strategy": "partial_refresh"},
    ])

    with (
        patch("src.components.local_cache_inspector.find_matching_sessions",
            return_value=[completed_session]),
        patch("src.components.local_cache_inspector._final_doc_exists", return_value=True),
        patch("src.components.local_cache_inspector._has_ingestion_files", return_value=False),
        patch("src.components.local_cache_inspector.get_session_meta",
            return_value=completed_session),
        patch("src.components.local_cache_inspector.interrupt",
            side_effect=interrupt_responses),
        patch("src.components.local_cache_inspector._run_update_check",
            new_callable=AsyncMock, return_value=mock_assessment),
        patch("src.components.local_cache_inspector._read_previous_summary",
            return_value="Old docs."),
        patch("src.components.local_cache_inspector.copy_scratchpad_from",
            return_value=True) as mock_copy,
        patch("src.components.local_cache_inspector.write_scratchpad"),
        patch("src.components.local_cache_inspector.put_session_meta"),
    ):
        from src.components.local_cache_inspector import local_cache_inspector_node, _REUSE_TABLE

        result = await local_cache_inspector_node(base_state)

    assert result["cache_decision"] == "partial_refresh"
    assert result["is_update"] is True

    # For "minor": only chapter_planner should be reused
    reuse_nodes = _REUSE_TABLE["minor"]
    assert "chapter_planner" in reuse_nodes
    # quality_judge should NOT be in reuse_nodes for "minor"
    assert "quality_judge" not in reuse_nodes

    # copy_scratchpad_from called for each reuse node
    copied_nodes = result.get("completed_nodes", set())
    for node in reuse_nodes:
        assert node in copied_nodes


# Test 5: State A + update + cancel

@pytest.mark.asyncio
async def test_state_a_update_cancel(base_state):
    """User chooses 'update', sees assessment, then cancels → view path."""
    completed_session = _make_session("source-thread-004")

    mock_assessment = {
        "is_significant":     False,
        "significance_level": "patch",
        "summary":            "Only bug fixes.",
        "new_releases":       [],
        "breaking_changes":   [],
        "new_features":       [],
        "recommendation":     "no_update",
        "update_check_available": True,
    }

    interrupt_responses = iter([
        {"decision": "update",        "source_thread_id": "source-thread-004"},
        {"decision": "cancel_update", "refresh_strategy": None},
    ])

    with (
        patch("src.components.local_cache_inspector.find_matching_sessions",
            return_value=[completed_session]),
        patch("src.components.local_cache_inspector._final_doc_exists", return_value=True),
        patch("src.components.local_cache_inspector._has_ingestion_files", return_value=False),
        patch("src.components.local_cache_inspector.get_session_meta",
            return_value=completed_session),
        patch("src.components.local_cache_inspector.interrupt",
            side_effect=interrupt_responses),
        patch("src.components.local_cache_inspector._run_update_check",
            new_callable=AsyncMock, return_value=mock_assessment),
        patch("src.components.local_cache_inspector.write_scratchpad"),
        patch("src.components.local_cache_inspector.put_session_meta"),
    ):
        from src.components.local_cache_inspector import local_cache_inspector_node

        result = await local_cache_inspector_node(base_state)

    assert result["cache_decision"] == "view"
    assert result["cache_source_thread_id"] == "source-thread-004"


# Test 6: State B — partial cache + use_partial
@pytest.mark.asyncio
async def test_state_b_use_partial(base_state):
    """Only partial sessions found; user chooses 'use_partial'.
    Verify copy_scratchpad_from called for each completed node in source."""
    partial_session = _make_session(
        "source-thread-005",
        status="running",
        completed_nodes=["web_discovery", "confirm_package", "github_agent"],
    )
    partial_session["last_completed_node"] = "github_agent"

    with (
        patch("src.components.local_cache_inspector.find_matching_sessions",
            return_value=[partial_session]),
        patch("src.components.local_cache_inspector._final_doc_exists", return_value=False),
        patch("src.components.local_cache_inspector._has_ingestion_files", return_value=True),
        patch("src.components.local_cache_inspector.get_session_meta",
            return_value=partial_session),
        patch("src.components.local_cache_inspector.interrupt",
            return_value={"decision": "use_partial", "source_thread_id": "source-thread-005"}),
        patch("src.components.local_cache_inspector.copy_scratchpad_from",
            return_value=True) as mock_copy,
        patch("src.components.local_cache_inspector.read_scratchpad", return_value=None),
        patch("src.components.local_cache_inspector.write_scratchpad"),
        patch("src.components.local_cache_inspector.put_session_meta"),
    ):
        from src.components.local_cache_inspector import local_cache_inspector_node

        result = await local_cache_inspector_node(base_state)

    assert result["cache_decision"] == "use_partial"
    assert result["cache_source_thread_id"] == "source-thread-005"

    # copy_scratchpad_from must be called for each completed node in source
    copied_calls = [c.args[2] for c in mock_copy.call_args_list]
    for node in ["web_discovery", "confirm_package", "github_agent"]:
        assert node in copied_calls, f"Expected copy call for {node}"


# Test 7: GitHub rate-limit → update_check_available == False
@pytest.mark.asyncio
async def test_github_rate_limit(base_state):
    """GitHub API returns 429 → update_check_available=False, pipeline continues."""
    import httpx

    completed_session = _make_session("source-thread-006")

    interrupt_responses = iter([
        {"decision": "update",         "source_thread_id": "source-thread-006"},
        {"decision": "proceed_update", "refresh_strategy": "partial_refresh"},
    ])

    # Simulate a 429 response from httpx
    mock_response_429 = MagicMock()
    mock_response_429.status_code = 429

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_response_429)

    with (
        patch("src.components.local_cache_inspector.find_matching_sessions",
            return_value=[completed_session]),
        patch("src.components.local_cache_inspector._final_doc_exists", return_value=True),
        patch("src.components.local_cache_inspector._has_ingestion_files", return_value=False),
        patch("src.components.local_cache_inspector.get_session_meta",
            return_value=completed_session),
        patch("src.components.local_cache_inspector.interrupt",
            side_effect=interrupt_responses),
        patch("httpx.AsyncClient", return_value=mock_client),
        patch("src.components.local_cache_inspector._read_previous_summary",
            return_value=""),
        patch("src.components.local_cache_inspector.copy_scratchpad_from", return_value=True),
        patch("src.components.local_cache_inspector.write_scratchpad"),
        patch("src.components.local_cache_inspector.put_session_meta"),
    ):
        from src.components.local_cache_inspector import local_cache_inspector_node

        # Must not raise — graceful degradation
        result = await local_cache_inspector_node(base_state)

    # Pipeline should continue (not crash); cache_decision should be set
    assert result.get("cache_decision") in ("partial_refresh", "full_refresh", "view", "regenerate")
    # The update_assessment should show update_check_available=False
    assessment = result.get("update_assessment", {})
    assert assessment.get("update_check_available") is False
