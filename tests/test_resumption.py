"""
Tests for:
- src/graph/resumption.py  — skippable() decorator
- src/components/resumption_inspector.py — resumption_inspector_node
"""
import json
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def base_state():
    return {
        "thread_id": "test-thread-1",
        "user_id": "user-42",
        "completed_nodes": set(),
        "scratchpad_files": [],
        "messages": [],
        "package_name": "requests",
        "language": "Python",
        "ecosystem": "PyPI",
    }


# ─── skippable() decorator ────────────────────────────────────────────────────

class TestSkippableSync:
    def test_skips_when_node_in_completed_nodes(self, base_state):
        from src.graph.resumption import skippable

        @skippable("my_node")
        def node_fn(state):
            return {"result": "ran"}

        state = {**base_state, "completed_nodes": {"my_node"}}
        result = node_fn(state)
        assert result == {}

    def test_runs_when_node_not_in_completed_nodes(self, base_state):
        from src.graph.resumption import skippable

        @skippable("my_node")
        def node_fn(state):
            return {"result": "ran"}

        result = node_fn(base_state)
        assert result["result"] == "ran"

    def test_adds_node_to_completed_nodes_after_run(self, base_state):
        from src.graph.resumption import skippable

        @skippable("my_node")
        def node_fn(state):
            return {"result": "ran"}

        result = node_fn(base_state)
        assert "my_node" in result["completed_nodes"]

    def test_merges_existing_completed_nodes(self, base_state):
        from src.graph.resumption import skippable

        @skippable("my_node")
        def node_fn(state):
            return {"completed_nodes": {"prior_node"}}

        result = node_fn(base_state)
        assert "my_node" in result["completed_nodes"]
        assert "prior_node" in result["completed_nodes"]

    def test_missing_completed_nodes_key_treated_as_empty(self):
        from src.graph.resumption import skippable

        @skippable("my_node")
        def node_fn(state):
            return {"value": 1}

        result = node_fn({"thread_id": "x"})
        assert "my_node" in result["completed_nodes"]

    def test_does_not_catch_exceptions(self, base_state):
        from src.graph.resumption import skippable

        @skippable("my_node")
        def node_fn(state):
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            node_fn(base_state)


class TestSkippableAsync:
    @pytest.mark.asyncio
    async def test_skips_when_node_in_completed_nodes(self, base_state):
        from src.graph.resumption import skippable

        @skippable("async_node")
        async def node_fn(state):
            return {"result": "ran"}

        state = {**base_state, "completed_nodes": {"async_node"}}
        result = await node_fn(state)
        assert result == {}

    @pytest.mark.asyncio
    async def test_runs_when_node_not_in_completed_nodes(self, base_state):
        from src.graph.resumption import skippable

        @skippable("async_node")
        async def node_fn(state):
            return {"result": "async-ran"}

        result = await node_fn(base_state)
        assert result["result"] == "async-ran"

    @pytest.mark.asyncio
    async def test_adds_node_to_completed_nodes(self, base_state):
        from src.graph.resumption import skippable

        @skippable("async_node")
        async def node_fn(state):
            return {}

        result = await node_fn(base_state)
        assert "async_node" in result["completed_nodes"]

    @pytest.mark.asyncio
    async def test_does_not_catch_exceptions(self, base_state):
        from src.graph.resumption import skippable

        @skippable("async_node")
        async def node_fn(state):
            raise ValueError("async boom")

        with pytest.raises(ValueError, match="async boom"):
            await node_fn(base_state)


# ─── resumption_inspector_node ───────────────────────────────────────────────

# Patch targets for all tests in this section
_INSPECTOR_MODULE = "src.components.resumption_inspector"


@pytest.fixture
def mock_store():
    return MagicMock()


@pytest.mark.asyncio
async def test_inspector_fresh_session(base_state, mock_store):
    with (
        patch(f"{_INSPECTOR_MODULE}.list_completed_nodes", return_value=set()),
        patch(f"{_INSPECTOR_MODULE}.put_session_meta") as mock_put,
        patch(f"{_INSPECTOR_MODULE}.global_store", mock_store),
    ):
        from src.components.resumption_inspector import resumption_inspector_node
        result = await resumption_inspector_node(base_state)

    assert result["is_resuming"] is False
    assert result["completed_nodes"] == set()
    assert result["scratchpad_dir"] == "sessions/test-thread-1"
    assert "Starting fresh" in result["resumption_summary"]
    mock_put.assert_called_once()


@pytest.mark.asyncio
async def test_inspector_restores_web_discovery(base_state, mock_store):
    search_results = [{"title": "requests", "url": "https://github.com/psf/requests"}]

    with (
        patch(f"{_INSPECTOR_MODULE}.list_completed_nodes",
              return_value={"web_discovery"}),
        patch(f"{_INSPECTOR_MODULE}.read_scratchpad",
              return_value=json.dumps(search_results)),
        patch(f"{_INSPECTOR_MODULE}.put_session_meta"),
        patch(f"{_INSPECTOR_MODULE}.global_store", mock_store),
    ):
        from importlib import reload
        import src.components.resumption_inspector as mod
        result = await mod.resumption_inspector_node(base_state)

    assert result["search_results"] == search_results
    assert result["is_resuming"] is True


@pytest.mark.asyncio
async def test_inspector_restores_confirm_package(base_state, mock_store):
    pkg_data = {
        "confirmed_package": {"title": "requests"},
        "github_url": "https://github.com/psf/requests",
        "docs_url": "https://docs.python-requests.org",
    }

    def fake_read_scratchpad(thread_id, node_name):
        if node_name == "confirm_package":
            return json.dumps(pkg_data)
        return None

    with (
        patch(f"{_INSPECTOR_MODULE}.list_completed_nodes",
              return_value={"confirm_package"}),
        patch(f"{_INSPECTOR_MODULE}.read_scratchpad",
              side_effect=fake_read_scratchpad),
        patch(f"{_INSPECTOR_MODULE}.put_session_meta"),
        patch(f"{_INSPECTOR_MODULE}.global_store", mock_store),
    ):
        import src.components.resumption_inspector as mod
        result = await mod.resumption_inspector_node(base_state)

    assert result["confirmed_package"] == {"title": "requests"}
    assert result["github_url"] == "https://github.com/psf/requests"
    assert result["docs_url"] == "https://docs.python-requests.org"


@pytest.mark.asyncio
async def test_inspector_restores_quality_report(base_state, mock_store):
    from src.components.quality_judge import DimensionScore

    quality_data = {
        "quality_score": 0.6,
        "quality_report": {
            "api_coverage": {"score": 3.0, "reasoning": "Decent coverage", "gaps": ["missing types"]},
            "beginner_friendliness": {"score": 2.0, "reasoning": "Sparse intro", "gaps": ["no quickstart"]},
        },
    }

    def fake_read_scratchpad(thread_id, node_name):
        if node_name == "quality_judge":
            return json.dumps(quality_data)
        return None

    with (
        patch(f"{_INSPECTOR_MODULE}.list_completed_nodes",
              return_value={"quality_judge"}),
        patch(f"{_INSPECTOR_MODULE}.read_scratchpad",
              side_effect=fake_read_scratchpad),
        patch(f"{_INSPECTOR_MODULE}.put_session_meta"),
        patch(f"{_INSPECTOR_MODULE}.global_store", mock_store),
    ):
        import src.components.resumption_inspector as mod
        result = await mod.resumption_inspector_node(base_state)

    assert result["quality_score"] == pytest.approx(0.6)
    report = result["quality_report"]
    assert isinstance(report["api_coverage"], DimensionScore)
    assert isinstance(report["beginner_friendliness"], DimensionScore)
    # Verify .gaps attribute is accessible (would fail if plain dict)
    assert "missing types" in report["api_coverage"].gaps
    assert "no quickstart" in report["beginner_friendliness"].gaps


@pytest.mark.asyncio
async def test_inspector_restores_chapter_plan(base_state, mock_store):
    chapters = [{"title": "Getting Started", "description": "Intro"}, {"title": "Advanced", "description": "Deep"}]
    chapter_plan = ["Getting Started", "Advanced"]
    plan_data = {"chapters": chapters, "chapter_plan": chapter_plan}

    def fake_read_scratchpad(thread_id, node_name):
        if node_name == "chapter_planner":
            return json.dumps(plan_data)
        return None

    with (
        patch(f"{_INSPECTOR_MODULE}.list_completed_nodes",
              return_value={"chapter_planner"}),
        patch(f"{_INSPECTOR_MODULE}.read_scratchpad",
              side_effect=fake_read_scratchpad),
        patch(f"{_INSPECTOR_MODULE}.put_session_meta"),
        patch(f"{_INSPECTOR_MODULE}.global_store", mock_store),
    ):
        import src.components.resumption_inspector as mod
        result = await mod.resumption_inspector_node(base_state)

    assert result["chapters"] == chapters
    assert result["chapter_plan"] == chapter_plan


@pytest.mark.asyncio
async def test_inspector_sets_is_resuming_with_completed_nodes(base_state, mock_store):
    completed = {"web_discovery", "confirm_package", "context7_agent", "docs_scraper", "github_agent"}

    def fake_read_scratchpad(thread_id, node_name):
        return None  # no state to restore; just testing completed_nodes tracking

    with (
        patch(f"{_INSPECTOR_MODULE}.list_completed_nodes", return_value=completed),
        patch(f"{_INSPECTOR_MODULE}.read_scratchpad", side_effect=fake_read_scratchpad),
        patch(f"{_INSPECTOR_MODULE}.put_session_meta"),
        patch(f"{_INSPECTOR_MODULE}.global_store", mock_store),
        # Prevent file system checks for scratchpad_file_paths
        patch("pathlib.Path.exists", return_value=False),
    ):
        import src.components.resumption_inspector as mod
        result = await mod.resumption_inspector_node(base_state)

    assert result["is_resuming"] is True
    assert result["completed_nodes"] == completed
    assert "Resuming session" in result["resumption_summary"]
