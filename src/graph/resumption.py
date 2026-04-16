"""
skippable() decorator for AgenticDocs nodes.

When a node's scratchpad file already exists (detected by resumption_inspector
and stored in state['completed_nodes']), the node is skipped entirely and
returns {} — the resumption_inspector has already loaded its output into state.

Rules:
- Works transparently with both sync and async node functions.
- Does NOT catch GraphInterrupt (raised by interrupt()) — it must propagate.
- Adds node_name to the returned dict's 'completed_nodes' set after a
  successful run so the state reducer can accumulate the full set.
"""
import functools
from typing import Any, Callable
import inspect


def skippable(node_name: str) -> Callable:
    """
    Decorator factory. Returns a decorator that wraps a node function.

    On each invocation:
    - If node_name is already in state['completed_nodes'], return {} immediately.
    - Otherwise call the original function; on success add node_name to
      the return dict's 'completed_nodes' (the state reducer does set-union).
    """

    def decorator(fn: Callable) -> Callable:
        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(state: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
                if node_name in state.get("completed_nodes", set()):
                    return {}
                result: dict[str, Any] = await fn(state, **kwargs)
                return _inject_completed(result, node_name)
            return async_wrapper
        else:
            @functools.wraps(fn)
            def sync_wrapper(state: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
                if node_name in state.get("completed_nodes", set()):
                    return {}
                result: dict[str, Any] = fn(state, **kwargs)
                return _inject_completed(result, node_name)
            return sync_wrapper

    return decorator


def _inject_completed(result: dict[str, Any], node_name: str) -> dict[str, Any]:
    """
    Merge node_name into result['completed_nodes'].

    The state reducer for completed_nodes is set-union, so returning just
    {node_name} is correct — the reducer accumulates it with the existing set.
    """
    if result is None:
        result = {}
    existing: set[str] = result.get("completed_nodes", set())
    return {**result, "completed_nodes": existing | {node_name}}
