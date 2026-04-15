"""
Tests for src/agents/chapter_planner.py — chapter_planner_node
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call


_PLANNER_MODULE = "src.agents.chapter_planner"


@pytest.fixture
def base_state():
    return {
        "thread_id": "test-thread-planner",
        "user_id": "user-1",
        "package_name": "httpx",
        "language": "Python",
        "ecosystem": "PyPI",
        "scratchpad_dir": "sessions/test-thread-planner",
        "completed_nodes": set(),
        "quality_report": {},
        "messages": [],
    }


def _make_chapter_plan(titles):
    """Build a mock ChapterPlan object."""
    from src.components.writer import ChapterSpec, ChapterPlan
    chapters = [
        ChapterSpec(
            slug=f"{i+1:02d}-{t.lower().replace(' ', '-')}",
            title=t,
            description=f"Description for {t}",
        )
        for i, t in enumerate(titles)
    ]
    return ChapterPlan(chapters=chapters)


@pytest.mark.asyncio
async def test_returns_chapters_and_plan(base_state):
    titles = ["Introduction", "Core Concepts", "Advanced Usage"]
    mock_plan = _make_chapter_plan(titles)

    with (
        patch(f"{_PLANNER_MODULE}._read_scratchpad_summary", return_value="summary text"),
        patch(f"{_PLANNER_MODULE}._planner") as mock_planner,
        patch(f"{_PLANNER_MODULE}.write_scratchpad"),
        patch(f"{_PLANNER_MODULE}.put_session_meta"),
    ):
        mock_planner.ainvoke = AsyncMock(return_value=mock_plan)
        from src.agents.chapter_planner import chapter_planner_node
        result = await chapter_planner_node(base_state)

    assert "chapters" in result
    assert "chapter_plan" in result
    assert len(result["chapters"]) == 3
    assert len(result["chapter_plan"]) == 3


@pytest.mark.asyncio
async def test_chapter_plan_matches_chapter_titles(base_state):
    titles = ["Getting Started", "API Reference", "Examples"]
    mock_plan = _make_chapter_plan(titles)

    with (
        patch(f"{_PLANNER_MODULE}._read_scratchpad_summary", return_value="summary"),
        patch(f"{_PLANNER_MODULE}._planner") as mock_planner,
        patch(f"{_PLANNER_MODULE}.write_scratchpad"),
        patch(f"{_PLANNER_MODULE}.put_session_meta"),
    ):
        mock_planner.ainvoke = AsyncMock(return_value=mock_plan)
        from src.agents.chapter_planner import chapter_planner_node
        result = await chapter_planner_node(base_state)

    for i, title in enumerate(titles):
        assert result["chapter_plan"][i] == title
        assert result["chapters"][i]["title"] == title


@pytest.mark.asyncio
async def test_writes_scratchpad_with_valid_json(base_state):
    titles = ["Ch1", "Ch2"]
    mock_plan = _make_chapter_plan(titles)

    with (
        patch(f"{_PLANNER_MODULE}._read_scratchpad_summary", return_value="summary"),
        patch(f"{_PLANNER_MODULE}._planner") as mock_planner,
        patch(f"{_PLANNER_MODULE}.write_scratchpad") as mock_write,
        patch(f"{_PLANNER_MODULE}.put_session_meta"),
    ):
        mock_planner.ainvoke = AsyncMock(return_value=mock_plan)
        from src.agents.chapter_planner import chapter_planner_node
        await chapter_planner_node(base_state)

    mock_write.assert_called_once()
    args = mock_write.call_args
    thread_id_arg, node_name_arg, content_arg = args[0]

    assert thread_id_arg == "test-thread-planner"
    assert node_name_arg == "chapter_planner"
    parsed = json.loads(content_arg)
    assert "chapters" in parsed
    assert "chapter_plan" in parsed
    assert parsed["chapter_plan"] == titles


@pytest.mark.asyncio
async def test_updates_session_meta_with_chapter_plan(base_state):
    titles = ["Overview", "Deep Dive"]
    mock_plan = _make_chapter_plan(titles)

    with (
        patch(f"{_PLANNER_MODULE}._read_scratchpad_summary", return_value="summary"),
        patch(f"{_PLANNER_MODULE}._planner") as mock_planner,
        patch(f"{_PLANNER_MODULE}.write_scratchpad"),
        patch(f"{_PLANNER_MODULE}.put_session_meta") as mock_put,
    ):
        mock_planner.ainvoke = AsyncMock(return_value=mock_plan)
        from src.agents.chapter_planner import chapter_planner_node
        await chapter_planner_node(base_state)

    mock_put.assert_called_once()
    _, _, updates = mock_put.call_args[0]
    assert updates["chapter_plan"] == titles
    assert updates["last_completed_node"] == "chapter_planner"


@pytest.mark.asyncio
async def test_skipped_when_in_completed_nodes(base_state):
    state = {**base_state, "completed_nodes": {"chapter_planner"}}

    with (
        patch(f"{_PLANNER_MODULE}._planner") as mock_planner,
        patch(f"{_PLANNER_MODULE}.write_scratchpad") as mock_write,
    ):
        mock_planner.ainvoke = AsyncMock()
        from src.agents.chapter_planner import chapter_planner_node
        result = await chapter_planner_node(state)

    assert result == {}
    mock_planner.ainvoke.assert_not_called()
    mock_write.assert_not_called()


@pytest.mark.asyncio
async def test_retries_once_on_failure_then_succeeds(base_state):
    titles = ["Intro", "Reference"]
    mock_plan = _make_chapter_plan(titles)

    with (
        patch(f"{_PLANNER_MODULE}._read_scratchpad_summary", return_value="summary"),
        patch(f"{_PLANNER_MODULE}._planner") as mock_planner,
        patch(f"{_PLANNER_MODULE}.write_scratchpad"),
        patch(f"{_PLANNER_MODULE}.put_session_meta"),
    ):
        # First call raises, second succeeds
        mock_planner.ainvoke = AsyncMock(side_effect=[Exception("parse error"), mock_plan])
        from src.agents.chapter_planner import chapter_planner_node
        result = await chapter_planner_node(base_state)

    assert result["chapter_plan"] == titles
    assert mock_planner.ainvoke.call_count == 2


@pytest.mark.asyncio
async def test_raises_value_error_after_two_failures(base_state):
    with (
        patch(f"{_PLANNER_MODULE}._read_scratchpad_summary", return_value="summary"),
        patch(f"{_PLANNER_MODULE}._planner") as mock_planner,
        patch(f"{_PLANNER_MODULE}.write_scratchpad"),
        patch(f"{_PLANNER_MODULE}.put_session_meta"),
    ):
        mock_planner.ainvoke = AsyncMock(side_effect=Exception("model error"))
        from src.agents.chapter_planner import chapter_planner_node
        with pytest.raises(ValueError, match="chapter_planner_node"):
            await chapter_planner_node(base_state)

    assert mock_planner.ainvoke.call_count == 2


@pytest.mark.asyncio
async def test_chapter_results_pre_initialized(base_state):
    """chapter_results must be initialized to [] for fan-out to work."""
    titles = ["Only Chapter"]
    mock_plan = _make_chapter_plan(titles)

    with (
        patch(f"{_PLANNER_MODULE}._read_scratchpad_summary", return_value="summary"),
        patch(f"{_PLANNER_MODULE}._planner") as mock_planner,
        patch(f"{_PLANNER_MODULE}.write_scratchpad"),
        patch(f"{_PLANNER_MODULE}.put_session_meta"),
    ):
        mock_planner.ainvoke = AsyncMock(return_value=mock_plan)
        from src.agents.chapter_planner import chapter_planner_node
        result = await chapter_planner_node(base_state)

    assert result.get("chapter_results") == []
