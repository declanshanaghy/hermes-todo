"""Call tracing and session reconcile: the observer half.

Every tool call a todo's worker makes is recorded with its input and
response (post_tool_call is emitted process-globally, and subagent
children are full in-process agent loops, so children fire it with their
own session ids — verified in the Hermes source). subagent_start links a
child to its todo by the launch's correlation id; subagent_stop ends the
agent row and settles the todo if the worker died without reporting; a
session that ends hands its in-flight todos to whoever goes on (the
stale reclaim).

Hot-path discipline: post_tool_call fires for every call in the process,
so the first check is an in-memory map, and nothing touches the database
unless the call belongs to one of ours.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# child_session_id -> (dir, n): the todos this process is tracing.
_linked: dict[str, tuple[str, int]] = {}
_loaded = False


def _store():
    from . import tools

    return tools.get_store()


def _load_linked() -> None:
    """Recover the link map after a process restart, from the agents table."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        store = _store()
        rows = store.conn.execute(
            "SELECT child_session_id, dir, n FROM agents WHERE status = 'running'"
        ).fetchall()
        for r in rows:
            _linked[r["child_session_id"]] = (r["dir"], r["n"])
    except Exception as exc:
        logger.warning("trace: could not load linked agents: %s", exc)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


# ---------- hooks (every callback accepts **kwargs: additive payloads) ----------


def on_subagent_start(**kwargs: Any) -> None:
    """A child launched with our correlation id works for a todo: link it."""
    from . import exec as runner_mod

    parsed = runner_mod.parse_correlation(kwargs.get("correlation_id"))
    child = kwargs.get("child_session_id")
    if parsed is None or not child:
        return
    dir, n = parsed
    try:
        _store().link_agent(dir, n, str(child))
        _linked[str(child)] = (dir, n)
    except Exception as exc:
        logger.warning("trace: link of %s failed: %s", child, exc)


def on_post_tool_call(**kwargs: Any) -> None:
    """Record a linked child's tool call with its input and response."""
    sid = str(kwargs.get("session_id") or "")
    if sid not in _linked:
        _load_linked()  # cheap no-op after the first call
        if sid not in _linked:
            return
    dir, n = _linked[sid]
    status = str(kwargs.get("status") or "ok")
    try:
        _store().record_call(
            str(kwargs.get("tool_call_id") or ""),
            sid,
            str(kwargs.get("tool_name") or ""),
            _text(kwargs.get("args")),
            _text(kwargs.get("result")),
            "err" if status not in ("ok", "success", "") else "ok",
            kwargs.get("duration_ms"),
        )
    except Exception as exc:
        logger.warning("trace: call record failed: %s", exc)


def on_subagent_stop(**kwargs: Any) -> None:
    """A worker ended: close its agent row, and settle its todo if it died
    without reporting (a lone worker's end never leaves a todo running)."""
    child = str(kwargs.get("child_session_id") or "")
    _load_linked()
    info = _linked.pop(child, None)
    if info is None:
        try:
            agent = _store().agent_of(child)
        except Exception:
            agent = None
        if agent is None:
            return
        info = (agent["dir"], agent["n"])
    dir, n = info

    status = str(kwargs.get("child_status") or "").upper()
    summary = kwargs.get("child_summary") or ""
    agent_status = {
        "SUCCEEDED": "done", "FAILED": "failed", "INTERRUPTED": "stopped",
        "CANCELLED": "stopped",
    }.get(status, "stopped")
    try:
        store = _store()
        store.end_agent(child, agent_status, str(summary))
        todo = store.get(dir, n)
        if todo is not None and todo["status"] == "running":
            # The worker's own todo_update report wins; this catches a
            # worker that ended without one.
            store.update(
                dir,
                n,
                {
                    "status": {"SUCCEEDED": "done", "FAILED": "failed"}.get(status, "stopped"),
                    "summary": (str(summary)[:200] or f"worker ended {status or 'quietly'}"),
                },
            )
    except Exception as exc:
        logger.warning("trace: settle of %s failed: %s", child, exc)


def on_session_start(**kwargs: Any) -> None:
    """A session starts: take back todos whose owner session has ended."""
    me = str(kwargs.get("session_id") or "")
    if not me:
        return
    try:
        from . import tools

        store = _store()
        gone = store.reclaim_stale(tools.list_dir(), me)
        if gone:
            logger.info("reclaimed stale todos %s from ended sessions", gone)
    except Exception as exc:
        logger.warning("trace: reclaim failed: %s", exc)


def on_session_end(**kwargs: Any) -> None:
    """A session ended: remember it, so a later one can take its todos."""
    sid = str(kwargs.get("session_id") or "")
    if not sid:
        return
    try:
        _store().note_session_end(sid)
    except Exception as exc:
        logger.warning("trace: session end note failed: %s", exc)


def on_session_reset(**kwargs: Any) -> None:
    """A /clear or /new: the old session id is the one that ended."""
    sid = str(kwargs.get("old_session_id") or kwargs.get("session_id") or "")
    if not sid:
        return
    try:
        _store().note_session_end(sid)
    except Exception as exc:
        logger.warning("trace: session reset note failed: %s", exc)
