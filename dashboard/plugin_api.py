"""Backend API routes for the hermes-todo pane, mounted by ``hermes
dashboard`` and the Desktop app at ``/api/plugins/hermes-todo/``.

- ``GET  /backlog?dir=<repo>`` — the list with each todo's public fields,
  its glyph and the chips its row offers (the widget draws what this says,
  so the pane and the /todo verbs can't drift)
- ``GET  /details?dir=<repo>&n=<n>`` — a todo's prompt, summary, workers
  and their recorded calls
- ``POST /action`` — the pane's store-side actions (add, rename, delete,
  reset, takeover, leave). The run verbs (exec, auto, resume, stop, …)
  belong to the session that owns the todo, so the route answers with a
  note to type the /todo verb in the chat — the TUI-mode data bridge is
  the one open seam (docs/hermes-orchestration.md, "The data path").

The dashboard serves this from its own process, so the route opens the
same profile store directly (plugin_db) rather than going through a
session's plugin context.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter

router = APIRouter()

# The plugin's own modules sit one directory up; load them as the package
# (the directory name has a hyphen, like the test suite does).
_ROOT = Path(__file__).resolve().parent.parent


def _plugin() -> Any:
    if "hermes_todo" in sys.modules:
        return sys.modules["hermes_todo"]
    spec = importlib.util.spec_from_file_location(
        "hermes_todo", _ROOT / "__init__.py", submodule_search_locations=[str(_ROOT)]
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load the hermes-todo package")
    pkg = importlib.util.module_from_spec(spec)
    sys.modules["hermes_todo"] = pkg
    spec.loader.exec_module(pkg)
    # __init__ keeps its imports inside register() (pytest loads this root
    # as a bare module), so pull in the modules the routes read directly.
    for name in ("db", "moves", "tools"):
        importlib.import_module(f"hermes_todo.{name}")
    return pkg


def _store() -> Any:
    from plugins.plugin_storage import plugin_db

    hermes_todo = _plugin()
    if getattr(hermes_todo.tools, "_STORE", None) is None:
        hermes_todo.tools._STORE = hermes_todo.db.Store(plugin_db("hermes-todo"))
    return hermes_todo.tools._STORE


def _glyphs(hermes_todo: Any, todos: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for t in todos:
        counts[t["status"]] = counts.get(t["status"], 0) + 1
    return counts


@router.get("/backlog")
async def backlog(dir: str = "", me: str = "") -> dict:
    """The repo's list: public fields, glyphs and offered chips per todo."""
    hermes_todo = _plugin()
    store = _store()
    if not dir:
        dir = hermes_todo.tools.list_dir()
    store.expire(dir, hermes_todo.db.done_ttl_minutes())
    todos = store.todos(dir)
    return {
        "dir": dir,
        "counts": _glyphs(hermes_todo, todos),
        "todos": [
            {
                **hermes_todo.db._public(t),
                "glyph": hermes_todo.moves.status_glyph(t["status"]),
                "offers": hermes_todo.moves.offers(t, me),
            }
            for t in todos
        ],
    }


@router.get("/details")
async def details(dir: str, n: int) -> dict:
    """A todo's prompt, summary, workers and their recorded calls."""
    hermes_todo = _plugin()
    store = _store()
    todo = store.get(dir, n)
    if todo is None:
        return {"error": f"no todo #{n} in this list"}
    agents = []
    for agent in store.agents_for(dir, n):
        agents.append(
            {
                "id": agent["child_session_id"],
                "status": agent["status"],
                "started_at": agent["started_at"],
                "ended_at": agent.get("ended_at"),
                "summary": agent.get("summary"),
                "calls": store.calls_for(agent["child_session_id"], limit=40),
            }
        )
    return {"todo": hermes_todo.db._public(todo), "agents": agents}


# The actions the pane may take here, in the dashboard's process: the ones
# that only touch the store. A run verb belongs to the session that owns
# the todo, so those relay to the gateway: it runs the same /todo dispatcher
# the chat does, against the session that last used this list.
_PANE_ACTIONS = {"add", "rename", "delete", "reset", "takeover", "leave"}
_RUN_VERBS = {"exec", "auto", "fire", "resume", "pause", "stop", "refine", "restart"}


async def _relay_run_verb(dir: str, verb: str, n: int | None) -> dict:
    """Hand a run verb to the gateway, which runs our /todo command in the
    session that last used this list (the same path as typing it). The
    gateway's dispatcher then launches or injects as if the person typed it."""
    import asyncio

    store = _store()
    sid = store.last_session(dir)
    if not sid:
        return {
            "ok": False,
            "note": "no session has used this list yet — type /todo list in your chat once, then press again",
        }
    try:
        async def call() -> str:
            from hermes_cli.gateway_client import connect_gateway

            async with connect_gateway() as client:
                reply = await client.rpc(
                    "slash.exec", session_id=sid, command=f"todo {verb} {n or ''}".strip()
                )
                return str((reply or {}).get("output") or "")

        output = await asyncio.wait_for(call(), timeout=20)
        await asyncio.to_thread(store.touch_session, dir, sid)
        return {"ok": True, "reply": output[:600]}
    except Exception as exc:
        return {"ok": False, "note": f"couldn't reach the session for this list: {exc}"}


def _num(value: Any) -> int | None:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


@router.post("/action")
async def action(body: dict) -> dict:
    hermes_todo = _plugin()
    verb = str(body.get("verb") or "").strip().lower()
    dir = str(body.get("dir") or "")
    n = _num(body.get("n"))
    if verb in _RUN_VERBS:
        return await _relay_run_verb(dir, verb, n)
    if verb not in _PANE_ACTIONS:
        return {"ok": False, "error": f"unknown action {verb!r}"}
    try:
        store = _store()
        if not dir:
            dir = hermes_todo.tools.list_dir()
        if verb == "add":
            title = str(body.get("title") or "").strip()
            if not title:
                return {"ok": False, "error": "a todo needs a title"}
            return {"ok": True, "added": store.add(dir, title)}
        if verb == "rename":
            title = str(body.get("title") or "").strip()
            if not title or n is None:
                return {"ok": False, "error": "rename needs n and title"}
            res = store.update(dir, n, {"title": title})
            return {k: v for k, v in res.items() if k != "todo"}
        if n is None:
            return {"ok": False, "error": f"{verb} needs n"}
        if verb == "delete":
            return store.delete(dir, n)
        if verb == "reset":
            todo = store.get(dir, n)
            if todo is None:
                return {"error": f"no todo #{n} in this list"}
            to = "stopped" if todo["has_run"] else "pending"
            res = store.update(dir, n, {"status": to, "owner": None})
            return {k: v for k, v in res.items() if k != "todo"}
        if verb == "takeover":
            res = store.update(
                dir, n,
                {"status": "stopped", "task_id": None, "handle": None, "run_id": None,
                 "summary": "taken over from the pane"},
            )
            return {k: v for k, v in res.items() if k != "todo"}
        # leave: the pane folds a just-stopped row; the row itself stands
        return {"ok": True, "n": n, "folded": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
