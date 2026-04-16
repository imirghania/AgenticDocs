"""
Tests for writer enhancements:
  - extract_chapter_metadata correctness and malformed-input safety
  - ThoroughnessReview parse-failure fallback (no raise)
  - Revision loop terminates after 2 iterations even if reviewer always says "revise"
"""
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.components.writer import (
    MAX_REVIEW_ITERATIONS,
    extract_chapter_metadata,
    write_review_chapter_node,
)
from src.schemas.writing import CriterionResult, ThoroughnessReview


# ── Sample markdown fixture ───────────────────────────────────────────────────

_SAMPLE_MD = """\
## Overview

**Event loop** (the central scheduler that runs coroutines) is at the heart of asyncio.

**Analogy:**
> Think of the event loop like a restaurant waiter who handles many tables by switching
> attention between them whenever one table is waiting for their food.

A **coroutine** (a function that can be paused and resumed) lets you write async code.

**Analogy:**
> A coroutine is similar to a generator in Python — it yields control back to the caller
> and can be resumed from where it left off.

## Usage

Call `asyncio.run()` to execute your top-level coroutine.

### Key terms

**event loop** — The central scheduler that runs coroutines and callbacks.
**coroutine** — A function that can be paused and resumed, defined with `async def`.
"""


# ── Tests for extract_chapter_metadata ───────────────────────────────────────

class TestExtractChapterMetadata:
    def test_valid_markdown_terms(self) -> None:
        result = extract_chapter_metadata(_SAMPLE_MD)
        terms: dict[str, str] = result["defined_terms"]  # type: ignore[assignment]
        assert "event loop" in terms
        assert "coroutine" in terms
        assert terms["event loop"] == "The central scheduler that runs coroutines and callbacks."
        assert terms["coroutine"] == "A function that can be paused and resumed, defined with `async def`."

    def test_valid_markdown_analogies(self) -> None:
        result = extract_chapter_metadata(_SAMPLE_MD)
        analogies: list[str] = result["analogies"]  # type: ignore[assignment]
        assert len(analogies) == 2
        assert "restaurant waiter" in analogies[0]
        assert "generator" in analogies[1]

    def test_malformed_empty_string(self) -> None:
        result = extract_chapter_metadata("")
        assert result["defined_terms"] == {}
        assert result["analogies"] == []

    def test_malformed_no_sections(self) -> None:
        result = extract_chapter_metadata("Just some plain text with no structure.")
        assert result["defined_terms"] == {}
        assert result["analogies"] == []

    def test_partial_key_terms_no_analogies(self) -> None:
        md = "## Section\n\nSome text.\n\n### Key terms\n**foo** — a foo thing.\n"
        result = extract_chapter_metadata(md)
        assert result["defined_terms"] == {"foo": "a foo thing."}
        assert result["analogies"] == []

    def test_never_raises_on_garbage_input(self) -> None:
        # Should not raise regardless of input
        for bad in [None, 12345, b"bytes", ["list"], {"key": "val"}]:  # type: ignore[list-item]
            try:
                extract_chapter_metadata(bad)  # type: ignore[arg-type]
            except Exception as exc:
                pytest.fail(f"extract_chapter_metadata raised on {bad!r}: {exc}")


# ── Shared state fixture for write_review_chapter_node ───────────────────────

def _make_state(chapter_title: str = "Overview") -> dict[str, Any]:
    return {
        "package_name": "mylib",
        "language": "Python",
        "ecosystem": "pypi",
        "thread_id": "test-thread",
        "user_id": "test-user",
        "scratchpad_dir": "/tmp/test-scratchpad",
        "current_chapter": {
            "slug": "01-overview",
            "title": chapter_title,
            "description": "Introduce the library.",
        },
        "defined_terms": {},
        "chapter_analogies": {},
        "chapter_review_results": {},
        "chapters_revised": [],
        "completed_nodes": set(),
        "is_update": False,
        "previous_doc_summary": None,
        "messages": [("user", "document mylib")],
    }


def _pass_review(title: str = "Overview") -> ThoroughnessReview:
    crit = CriterionResult(verdict="pass", notes="OK", revisions=[])
    return ThoroughnessReview(
        chapter_title=title,
        criteria={
            "concept_completeness": crit,
            "key_term_coverage": crit,
            "analogy_presence": crit,
            "example_completeness": crit,
            "progressive_explanation": crit,
        },
        overall_verdict="pass",
        revision_summary="",
    )


def _fail_review(title: str = "Overview") -> ThoroughnessReview:
    fail_crit = CriterionResult(verdict="fail", notes="Missing example.", revisions=["Add a code example."])
    pass_crit = CriterionResult(verdict="pass", notes="OK", revisions=[])
    return ThoroughnessReview(
        chapter_title=title,
        criteria={
            "concept_completeness": fail_crit,
            "key_term_coverage": pass_crit,
            "analogy_presence": pass_crit,
            "example_completeness": pass_crit,
            "progressive_explanation": pass_crit,
        },
        overall_verdict="revise",
        revision_summary="Add a code example.",
    )


# ── Test: reviewer parse failure falls back gracefully ───────────────────────

@pytest.mark.asyncio
async def test_reviewer_json_parse_fallback(tmp_path: Any) -> None:
    """If the reviewer LLM raises, the node should accept the draft without raising."""
    state = _make_state()

    draft_content = "# Overview\nSome content.\n\n### Key terms\n**mylib** — the library.\n"

    with (
        patch("src.components.writer._invoke_writer", new_callable=AsyncMock) as mock_writer,
        patch("src.components.writer._invoke_reviewer", new_callable=AsyncMock) as mock_reviewer,
        patch("src.components.writer._output_dir") as mock_output_dir,
        patch("src.components.writer._read_scratchpad_summary", return_value="source"),
    ):
        chapter_path = tmp_path / "01-overview.md"
        chapter_path.write_text(draft_content)
        mock_output_dir.return_value = tmp_path
        mock_writer.return_value = draft_content
        mock_reviewer.side_effect = ValueError("Malformed JSON from LLM")

        result = await write_review_chapter_node(state)

    results: list[dict[str, Any]] = result["chapter_results"]
    assert results[0]["accepted"] is True   # accepted despite reviewer failure
    assert results[0]["iterations"] == 1


# ── Test: revision loop terminates after 2 iterations ────────────────────────

@pytest.mark.asyncio
async def test_revision_loop_terminates_after_two_iterations(tmp_path: Any) -> None:
    """
    Even if the reviewer always returns 'revise', the loop must stop after
    MAX_REVIEW_ITERATIONS (2) and accept the draft.
    """
    assert MAX_REVIEW_ITERATIONS == 2, "This test assumes MAX_REVIEW_ITERATIONS == 2"

    state = _make_state()
    draft_content = "# Overview\nContent.\n\n### Key terms\n**mylib** — lib.\n"
    call_count = 0

    async def always_fail_reviewer(messages: Any) -> ThoroughnessReview:
        nonlocal call_count
        call_count += 1
        return _fail_review()

    with (
        patch("src.components.writer._invoke_writer", new_callable=AsyncMock) as mock_writer,
        patch("src.components.writer._invoke_reviewer", side_effect=always_fail_reviewer),
        patch("src.components.writer._output_dir") as mock_output_dir,
        patch("src.components.writer._read_scratchpad_summary", return_value="source"),
    ):
        chapter_path = tmp_path / "01-overview.md"
        chapter_path.write_text(draft_content)
        mock_output_dir.return_value = tmp_path
        mock_writer.return_value = draft_content

        result = await write_review_chapter_node(state)

    # Reviewer called exactly MAX_REVIEW_ITERATIONS times
    assert call_count == MAX_REVIEW_ITERATIONS
    results: list[dict[str, Any]] = result["chapter_results"]
    assert results[0]["accepted"] is True      # accepted after hitting limit
    assert results[0]["iterations"] == MAX_REVIEW_ITERATIONS
    # chapter was revised (iteration > 1)
    assert state["current_chapter"]["title"] in result.get("chapters_revised", [result["chapters_revised"]])  # type: ignore[index]
