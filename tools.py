"""Tool handlers — the code that runs when the LLM calls each tool.

Rules: receive ``args`` (dict), accept ``**kwargs`` (Hermes injects
``session_id`` and friends additively), never raise, and always answer
with a JSON string, errors included.

``LLM`` is the session's ctx.llm facade, captured at register() — the
drafter's lane. Without it (no LLM lane available), a todo stays pending
and Exec drafts its prompt in the chat instead.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from . import db, drafter

_STORE: db.Store | None = None
LLM: Any = None  # set at register(); the drafter's lane


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
    """Add a todo, and draft its prompts in the background while the LLM
    lane is up (the todo is prompting until they're stored)."""
    title = (args.get("title") or "").strip()
    if not title:
        return _answer({"error": "usage: todo_add {\"title\": \"…\"}"})
    try:
        store = get_store()
        dir = list_dir()
        n = store.add(dir, title)
        key = None
        if LLM is not None:
            key = store.start_drafting(dir, n)
            if key:
                others = [t["title"] for t in store.todos(dir) if t["n"] != n]
                drafter.schedule(dir, n, title, key, others)
        todo = store.get(dir, n)
        return _answer({
            "ok": True, "added": n, "title": title,
            "status": todo["status"], "drafting": key is not None,
        })
    except Exception as exc:  # never raise out of a tool
        return _answer({"error": f"add failed: {exc}"})


def todo_backlog(args: dict, **kwargs) -> str:
    """The repo's backlog as JSON, after expiring done todos."""
    try:
        store = get_store()
        dir = list_dir()
        store.expire(dir, db.done_ttl_minutes())
        todos = store.todos(dir)
        return _answer(
            {
                "dir": dir,
                "count": len(todos),
                "todos": [db._public(t) for t in todos],
            }
        )
    except Exception as exc:
        return _answer({"error": f"list failed: {exc}"})


def todo_update(args: dict, **kwargs) -> str:
    """Report a todo's status and run details, through the guard. A title
    change renames: the drafts are cleared and drafted again."""
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
        dir = list_dir()
        result = store.update(dir, n, changes, me=me)
        if "error" in result:
            return _answer({"error": result["error"]})
        if "title" in changes and LLM is not None:
            status = (result.get("todo") or {}).get("status")
            if status in ("pending", "prompting"):
                # A rename clears the drafts and drafts again (R5); the old
                # drafter's late answer is stale under the new key.
                key = store.start_drafting(dir, n)
                if key:
                    others = [t["title"] for t in store.todos(dir) if t["n"] != n]
                    drafter.schedule(dir, n, changes["title"], key, others)
                    result["renamed"] = "drafting prompts again"
        return _answer(result)
    except ValueError as exc:
        return _answer({"error": str(exc)})
    except Exception as exc:
        return _answer({"error": f"update failed: {exc}"})
