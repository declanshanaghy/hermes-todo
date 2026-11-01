"""Tool handlers — the code that runs when the LLM calls each tool.

Rules: receive ``args`` (dict), accept ``**kwargs`` (Hermes injects
``session_id`` and friends additively), never raise, and always answer
with a JSON string, errors included.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from . import db

_STORE: db.Store | None = None


def get_store() -> db.Store:
    """The plugin's store: one SQLite database per profile, under the
    plugin data dir, surviving updates and removals."""
    global _STORE
    if _STORE is None:
        from plugins.plugin_storage import plugin_db

        _STORE = db.Store(plugin_db("hermes-todo"))
    return _STORE


def current_cwd() -> str:
    """The calling session's working directory, not the gateway's own."""
    try:
        from agent.runtime_cwd import resolve_agent_cwd

        return str(resolve_agent_cwd())
    except Exception:
        return str(Path.cwd())


def list_dir() -> str:
    """The directory a todo list belongs to: the git repo the session is in
    (each worktree is its own repo), else the home directory."""
    cwd = current_cwd()
    try:
        top = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if top.returncode == 0 and top.stdout.strip():
            return top.stdout.strip()
    except Exception:
        pass
    return str(Path.home())


def _answer(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def todo_add(args: dict, **kwargs) -> str:
    title = (args.get("title") or "").strip()
    if not title:
        return _answer({"error": "usage: todo_add {\"title\": \"…\"}"})
    try:
        store = get_store()
        n = store.add(list_dir(), title)
        return _answer({"ok": True, "added": n, "title": title, "status": "pending"})
    except Exception as exc:  # never raise out of a tool
        return _answer({"error": f"add failed: {exc}"})


def todo_backlog(args: dict, **kwargs) -> str:
    try:
        store = get_store()
        dir = list_dir()
        store.expire(dir, db.done_ttl_minutes())
        todos = store.todos(dir)
        return _answer(
            {
                "dir": dir,
                "count": len(todos),
                "todos": [
                    {
                        "n": t["n"],
                        "title": t["title"],
                        "status": t["status"],
                        "prompt": t["prompt"],
                        "mode": t["mode"],
                        "task_id": t["task_id"],
                        "run_id": t["run_id"],
                        "summary": t["summary"],
                        "branch": t["branch"],
                        "landed": t["landed"],
                        "has_run": t["has_run"],
                        "owner": t["owner"],
                    }
                    for t in todos
                ],
            }
        )
    except Exception as exc:
        return _answer({"error": f"list failed: {exc}"})


def todo_update(args: dict, **kwargs) -> str:
    try:
        n = int(args.get("n", 0))
    except (TypeError, ValueError):
        return _answer({"error": "usage: todo_update {\"n\": <number>, …}"})
    if not n:
        return _answer({"error": "usage: todo_update {\"n\": <number>, …}"})
    changes = {k: args[k] for k in args if k != "n" and args[k] is not None}
    me = str(kwargs.get("session_id") or "")
    try:
        store = get_store()
        result = store.update(list_dir(), n, changes, me=me)
        if "error" in result:
            return _answer({"error": result["error"]})
        return _answer(result)
    except ValueError as exc:
        return _answer({"error": str(exc)})
    except Exception as exc:
        return _answer({"error": f"update failed: {exc}"})
