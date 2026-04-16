"""
Long-term store for AgenticDocs session metadata and user preferences.

Priority:
  1. RedisStore  — if REDIS_URL env var is set
  2. FilesystemStore (default) — JSON files under sessions/meta/
     Survives process restarts with zero external dependencies.

Namespaces (logical, mapped to file paths for FilesystemStore):
  ("sessions", thread_id, "meta")            → session metadata dict
  ("sessions", thread_id, "scratchpad_index")→ node_name → filepath mapping
  ("users", user_id, "preferences")          → user preference dict
"""
import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# Filesystem-backed store
class _Item:
    """Minimal wrapper so callers can use item.value like InMemoryStore items."""
    __slots__ = ("value",)

    def __init__(self, value: Any) -> None:
        self.value = value


class FilesystemStore:
    """
    Persist store data as JSON files under a root directory.

    Layout:
        <root>/sessions/<thread_id>/meta/data.json
        <root>/sessions/<thread_id>/scratchpad_index/<node_name>.json
        <root>/users/<user_id>/preferences/defaults.json
    """

    def __init__(self, root: str | Path = "sessions/store") -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, namespace: tuple[str, ...], key: str) -> Path:
        p = self._root.joinpath(*namespace) / f"{key}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def get(self, namespace: tuple[str, ...], key: str) -> _Item | None:
        p = self._path(namespace, key)
        if not p.exists():
            return None
        try:
            return _Item(json.loads(p.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            return None

    def put(self, namespace: tuple[str, ...], key: str, value: Any) -> None:
        p = self._path(namespace, key)
        p.write_text(json.dumps(value, default=str), encoding="utf-8")

    def search(self, namespace: tuple[str, ...]) -> list[_Item]:
        """
        Return all items stored under any sub-path of namespace.
        For ("sessions",) this returns every data.json under sessions/*/meta/.
        """
        base = self._root.joinpath(*namespace)
        if not base.exists():
            return []
        items: list[_Item] = []
        for json_file in base.rglob("*.json"):
            try:
                items.append(_Item(json.loads(json_file.read_text(encoding="utf-8"))))
            except (json.JSONDecodeError, OSError):
                pass
        return items

    def delete(self, namespace: tuple[str, ...], key: str) -> None:
        """Delete a single key. No-op if the file doesn't exist."""
        p = self._path(namespace, key)
        if p.exists():
            p.unlink()

    def delete_prefix(self, namespace: tuple[str, ...]) -> None:
        """Delete all keys under a namespace subtree (shutil.rmtree the dir)."""
        base = self._root.joinpath(*namespace)
        if base.exists():
            shutil.rmtree(base)


# Store factory
def get_store() -> Any:
    """Return RedisStore if REDIS_URL is set, otherwise FilesystemStore."""
    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        try:
            from langgraph.store.redis import RedisStore  # type: ignore[import]
            return RedisStore.from_conn_string(redis_url)
        except ImportError:
            pass
    return FilesystemStore()


# Module-level singleton — imported by nodes and the Streamlit app.
store: Any = get_store()


# Session metadata helpers
def get_session_meta(the_store: Any, thread_id: str) -> dict | None:
    """Retrieve session metadata for thread_id, or None if not found."""
    item = the_store.get(("sessions", thread_id, "meta"), "data")
    return item.value if item else None


def put_session_meta(the_store: Any, thread_id: str, updates: dict) -> None:
    """
    Merge updates into the existing session meta dict and persist.
    Automatically sets 'updated_at' to the current UTC ISO timestamp.
    Creates a new entry if one doesn't exist yet.
    """
    existing = get_session_meta(the_store, thread_id) or {}
    merged = {
        **existing,
        **updates,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    the_store.put(("sessions", thread_id, "meta"), "data", merged)


def list_user_sessions(the_store: Any, user_id: str) -> list[dict]:
    """
    Return all session meta dicts for user_id, sorted by created_at descending.
    Falls back to an empty list on any store error.
    """
    try:
        items = the_store.search(("sessions",))
    except Exception:
        return []

    result: list[dict] = []
    for item in items:
        val: Any = item.value if hasattr(item, "value") else item
        if isinstance(val, dict) and val.get("user_id") == user_id:
            result.append(val)

    return sorted(result, key=lambda x: x.get("created_at", ""), reverse=True)


def delete_session(the_store: Any, thread_id: str) -> None:
    """
    Permanently delete all data for a session:
      1. Scratchpad files under sessions/{thread_id}/
      2. Store namespaces: meta + scratchpad_index (via delete_prefix)
    Does NOT delete user preferences or output/ chapter files.
    """
    meta = get_session_meta(the_store, thread_id) or {}
    package_name = meta.get("package_name", "unknown")

    # 1. Scratchpad directory
    scratchpad_path = Path("sessions") / thread_id
    if scratchpad_path.exists():
        shutil.rmtree(scratchpad_path)
    else:
        logging.warning("delete_session: scratchpad dir not found: %s", scratchpad_path)

    # 2. Store entries
    if hasattr(the_store, "delete_prefix"):
        the_store.delete_prefix(("sessions", thread_id))
    else:
        if hasattr(the_store, "delete"):
            the_store.delete(("sessions", thread_id, "meta"), "data")
            from src.graph.scratchpad import SCRATCHPAD_FILES  # noqa: PLC0415
            for node_name in SCRATCHPAD_FILES:
                try:
                    the_store.delete(
                        ("sessions", thread_id, "scratchpad_index"), node_name
                    )
                except Exception:
                    pass

    logging.info("Deleted session %s (%s)", thread_id, package_name)


# Scratchpad index helpers 
def update_scratchpad_index(
    the_store: Any, thread_id: str, node_name: str, filepath: str
) -> None:
    """Record that node_name wrote its output to filepath."""
    the_store.put(
        ("sessions", thread_id, "scratchpad_index"),
        node_name,
        {"filepath": filepath},
    )


# Session matching helpers
def find_matching_sessions(
    the_store: Any,
    package_name: str,
    github_url: str,
) -> list[dict]:
    """
    Return all sessions whose package_name matches (case-insensitive exact)
    and github_url normalises to the same canonical form, sorted by
    updated_at descending.

    Normalisation: lowercase, strip trailing slash, strip .git suffix.
    If github_url is empty/None, match on package_name only.
    """

    def _normalise(url: str) -> str:
        url = url.lower().strip().rstrip("/")
        if url.endswith(".git"):
            url = url[:-4]
        return url

    target_pkg = package_name.lower().strip()
    target_url = _normalise(github_url) if github_url else ""

    try:
        items = the_store.search(("sessions",))
    except Exception:
        return []

    result: list[dict] = []
    for item in items:
        val: Any = item.value if hasattr(item, "value") else item
        if not isinstance(val, dict):
            continue
        if val.get("package_name", "").lower().strip() != target_pkg:
            continue
        stored_url = _normalise(val.get("github_url", ""))
        if target_url and stored_url and stored_url != target_url:
            continue
        result.append(val)

    return sorted(result, key=lambda x: x.get("updated_at", ""), reverse=True)


# User preference helpers
def get_user_preferences(the_store: Any, user_id: str) -> dict:
    """Return user preference dict, with defaults if not found."""
    item = the_store.get(("users", user_id, "preferences"), "defaults")
    if item:
        return item.value
    return {
        "quality_threshold": 0.7,
        "output_format": "markdown",
        "confirmed_packages": [],
    }
