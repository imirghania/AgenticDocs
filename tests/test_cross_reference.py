"""
Tests for chapter cross-reference helpers:
  - build_concept_index: correct term→chapter mapping
  - _insert_concept_callbacks: first-occurrence only, skips code blocks, skips short terms
  - _generate_transitions: JSON parse failure does not crash, returns {}
"""
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.chapter_crossref import (
    build_concept_index,
    _insert_concept_callbacks,
    _generate_transitions,
    _insert_transition,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _write_chapter(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


# ── Tests: build_concept_index ────────────────────────────────────────────────

class TestBuildConceptIndex:
    def test_maps_terms_to_correct_chapters(self, tmp_path: Path) -> None:
        ch1 = _write_chapter(tmp_path, "01-overview.md",
            "## Intro\nText.\n\n### Key terms\n**event loop** — scheduler.\n**coroutine** — pausable fn.\n")
        ch2 = _write_chapter(tmp_path, "02-usage.md",
            "## Usage\nText.\n\n### Key terms\n**task** — scheduled coroutine.\n")

        idx = build_concept_index([ch1, ch2], ["Overview", "Usage"])

        assert idx["event loop"] == "Overview"
        assert idx["coroutine"] == "Overview"
        assert idx["task"] == "Usage"

    def test_first_chapter_wins_for_duplicate_term(self, tmp_path: Path) -> None:
        ch1 = _write_chapter(tmp_path, "01.md",
            "### Key terms\n**widget** — a UI element.\n")
        ch2 = _write_chapter(tmp_path, "02.md",
            "### Key terms\n**widget** — redefined here.\n")

        idx = build_concept_index([ch1, ch2], ["Ch1", "Ch2"])
        assert idx["widget"] == "Ch1"

    def test_empty_files_return_empty_index(self, tmp_path: Path) -> None:
        ch = _write_chapter(tmp_path, "01.md", "## No key terms section here.\n")
        idx = build_concept_index([ch], ["Ch1"])
        assert idx == {}

    def test_mismatched_chapter_plan_length(self, tmp_path: Path) -> None:
        """Extra chapter_plan entries beyond file list are ignored gracefully."""
        ch = _write_chapter(tmp_path, "01.md",
            "### Key terms\n**alpha** — first.\n")
        idx = build_concept_index([ch], ["Ch1", "Ch2 (no file)"])
        assert idx == {"alpha": "Ch1"}


# ── Tests: _insert_concept_callbacks ─────────────────────────────────────────

class TestInsertConceptCallbacks:
    _CONCEPT_INDEX = {
        "event loop": "Overview",
        "coroutine":  "Overview",
        "task":       "Usage",
    }

    def test_annotates_first_occurrence_only(self) -> None:
        text = (
            "The event loop runs things. "
            "You can use the event loop repeatedly. "
            "The event loop is central.\n"
        )
        modified, annotated = _insert_concept_callbacks(text, "Usage", self._CONCEPT_INDEX)

        # Only one annotation for "event loop"
        assert annotated.count("event loop") == 1 or "event loop" in annotated
        ref = "*(introduced in [Overview](#overview))*"
        assert modified.count(ref) == 1

    def test_skips_terms_inside_fenced_code_block(self) -> None:
        text = (
            "Here is an example:\n"
            "```python\n"
            "loop = event_loop.get_event_loop()\n"
            "```\n"
            "The above uses the built-in runner.\n"
        )
        modified, annotated = _insert_concept_callbacks(text, "Usage", self._CONCEPT_INDEX)
        # "event loop" appears only inside the code block — should not be annotated
        assert "event loop" not in annotated
        assert "*(introduced in" not in modified

    def test_skips_terms_inside_inline_code(self) -> None:
        text = "Call `event loop` to start. See docs.\n"
        modified, annotated = _insert_concept_callbacks(text, "Usage", self._CONCEPT_INDEX)
        assert "event loop" not in annotated

    def test_skips_terms_shorter_than_4_characters(self) -> None:
        index = {"io": "Overview", "api": "Overview", "event loop": "Overview"}
        text = "The io module and api calls use the event loop.\n"
        modified, annotated = _insert_concept_callbacks(text, "Usage", index)
        # "io" (2 chars) and "api" (3 chars) must be skipped
        assert "io" not in annotated
        assert "api" not in annotated
        # "event loop" (10 chars) should be annotated
        assert "event loop" in annotated

    def test_skips_own_chapter_terms(self) -> None:
        """Terms defined in the current chapter must not be annotated."""
        index = {"coroutine": "Usage"}  # coroutine belongs to this chapter
        text = "A coroutine is a function.\n"
        modified, annotated = _insert_concept_callbacks(text, "Usage", index)
        assert annotated == []
        assert modified == text

    def test_returns_unmodified_text_when_no_matches(self) -> None:
        text = "Nothing relevant here.\n"
        modified, annotated = _insert_concept_callbacks(text, "Ch X", self._CONCEPT_INDEX)
        assert annotated == []
        assert modified == text


# ── Tests: _generate_transitions ─────────────────────────────────────────────

class TestGenerateTransitions:
    @pytest.mark.asyncio
    async def test_returns_empty_dict_on_single_chapter(self, tmp_path: Path) -> None:
        ch = _write_chapter(tmp_path, "01.md", "Content.\n")
        result = await _generate_transitions([ch], ["Ch1"])
        assert result == {}

    @pytest.mark.asyncio
    async def test_parses_valid_json_response(self, tmp_path: Path) -> None:
        ch1 = _write_chapter(tmp_path, "01.md", "Chapter one content.\n")
        ch2 = _write_chapter(tmp_path, "02.md", "Chapter two content.\n")

        llm_json = json_response = (
            '[{"from_chapter": "Ch1", "to_chapter": "Ch2", "transition": "Now you know X."}]'
        )

        mock_response = MagicMock()
        mock_response.content = llm_json

        with patch("src.agents.chapter_crossref.llm") as mock_llm:
            mock_llm.ainvoke = AsyncMock(return_value=mock_response)
            result = await _generate_transitions([ch1, ch2], ["Ch1", "Ch2"])

        assert result == {"Ch1": "Now you know X."}

    @pytest.mark.asyncio
    async def test_json_parse_failure_returns_empty_dict(self, tmp_path: Path) -> None:
        """LLM returning non-JSON must not crash the node — returns {}."""
        ch1 = _write_chapter(tmp_path, "01.md", "Content.\n")
        ch2 = _write_chapter(tmp_path, "02.md", "Content.\n")

        mock_response = MagicMock()
        mock_response.content = "Sorry, I cannot do that."  # not valid JSON

        with patch("src.agents.chapter_crossref.llm") as mock_llm:
            mock_llm.ainvoke = AsyncMock(return_value=mock_response)
            result = await _generate_transitions([ch1, ch2], ["Ch1", "Ch2"])

        assert result == {}

    @pytest.mark.asyncio
    async def test_llm_exception_returns_empty_dict(self, tmp_path: Path) -> None:
        """LLM raising an exception must not crash the node — returns {}."""
        ch1 = _write_chapter(tmp_path, "01.md", "Content.\n")
        ch2 = _write_chapter(tmp_path, "02.md", "Content.\n")

        with patch("src.agents.chapter_crossref.llm") as mock_llm:
            mock_llm.ainvoke = AsyncMock(side_effect=RuntimeError("LLM unavailable"))
            result = await _generate_transitions([ch1, ch2], ["Ch1", "Ch2"])

        assert result == {}

    @pytest.mark.asyncio
    async def test_partial_json_still_yields_valid_pairs(self, tmp_path: Path) -> None:
        """JSON with some invalid entries — valid entries are still returned."""
        ch1 = _write_chapter(tmp_path, "01.md", "Content.\n")
        ch2 = _write_chapter(tmp_path, "02.md", "Content.\n")
        ch3 = _write_chapter(tmp_path, "03.md", "Content.\n")

        # Second pair entry is malformed (missing "transition" key)
        llm_json = (
            '[{"from_chapter": "Ch1", "to_chapter": "Ch2", "transition": "Bridge 1."},'
            ' {"from_chapter": "Ch2", "no_transition_key": true}]'
        )
        mock_response = MagicMock()
        mock_response.content = llm_json

        with patch("src.agents.chapter_crossref.llm") as mock_llm:
            mock_llm.ainvoke = AsyncMock(return_value=mock_response)
            result = await _generate_transitions([ch1, ch2, ch3], ["Ch1", "Ch2", "Ch3"])

        # First pair valid, second pair silently skipped
        assert result.get("Ch1") == "Bridge 1."
        assert "Ch2" not in result


# ── Tests: _insert_transition ─────────────────────────────────────────────────

class TestInsertTransition:
    def test_inserts_before_key_terms(self) -> None:
        text = "Body content.\n\n### Key terms\n**foo** — bar.\n"
        result = _insert_transition(text, "Transition paragraph.")
        assert "Transition paragraph." in result
        assert result.index("Transition paragraph.") < result.index("### Key terms")

    def test_appends_to_end_when_no_heading(self) -> None:
        text = "Body content.\n"
        result = _insert_transition(text, "Transition paragraph.")
        assert result.endswith("Transition paragraph.\n")
