"""The exec runner: each todo's worker is a Hermes child session launched
through ctx.subagent_lifecycle, working in its own git worktree, its goal
carrying the ported protocol prompt (the git rules, the todo_update
reporting rules) and the todo's number as its correlation id.

Launching outside an active agent turn fails closed in Hermes, so when a
worker can't be launched in-process (a /todo verb typed while the session
is idle), the runner falls back to handing the same protocol prompt to
the session's own agent as a queued user turn — the consented
``inject_message`` path. If neither works, the todo goes back where it
was and the error says so.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any, Callable

from . import protocol

logger = logging.getLogger(__name__)


def correlation_id(dir: str, n: int) -> str:
    """The todo's correlation id, carried by the launch and handed back by
    the subagent_start hook so the tracing half can link the child."""
    return f"todo:{dir}:{n}"


def parse_correlation(cid: str | None) -> tuple[str, int] | None:
    if not cid or not cid.startswith("todo:"):
        return None
    try:
        dir, n = cid[5:].rsplit(":", 1)
        return dir, int(n)
    except ValueError:
        return None


class Api:
    """The seam to ctx: subagent_lifecycle and inject_message. Tests pass a
    fake; the real one is built once at register()."""

    def launch(self, goal: str, working_directory: str | None, correlation: str) -> dict:
        raise NotImplementedError

    def cancel(self, handle: dict, reason: str) -> bool:
        return False

    def inject(self, text: str) -> bool:
        return False


class HermesApi(Api):
    def __init__(self, ctx: Any):
        self.ctx = ctx

    def launch(self, goal: str, working_directory: str | None, correlation: str) -> dict:
        service = self.ctx.subagent_lifecycle
        handle = service.launch(
            {
                "goal": goal,
                "role": "leaf",
                "working_directory": working_directory,
                "correlation_id": correlation,
            }
        )
        return handle.to_dict()

    def cancel(self, handle: dict, reason: str) -> bool:
        try:
            self.ctx.subagent_lifecycle.cancel(handle, reason=reason)
            return True
        except Exception as exc:
            logger.warning("cancel failed: %s", exc)
            return False

    def inject(self, text: str) -> bool:
        try:
            return bool(self.ctx.inject_message(text, role="user"))
        except Exception as exc:
            logger.warning("inject_message failed: %s", exc)
            return False


def _git(repo: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", repo, *args], capture_output=True, text=True, timeout=30
    )


class Runner:
    """Launches and halts todo workers over the guarded store."""

    def __init__(self, store_of: Callable[[], Any], api: Api):
        self.store_of = store_of
        self.api = api

    # ---------- git plumbing ----------

    def base_branch(self, repo: str) -> str:
        """The base branch: what the main checkout has checked out."""
        out = _git(repo, "branch", "--show-current")
        return out.stdout.strip() or "main"

    def worktree(self, repo: str, n: int, fresh: bool = False) -> str | None:
        """The todo's own worktree, created beside the repo (never inside
        it). An old one is kept where it is; a restart gets a new one."""
        if not repo or not Path(repo).is_dir():
            return None
        root = Path(repo)
        parent = root.parent / ".hermes-todo-worktrees"
        name = f"{root.name}-todo-{n}"
        path = parent / name
        if path.exists():
            if not fresh:
                return str(path)
            k = 2
            while (parent / f"{name}-r{k}").exists():
                k += 1
            name, path = f"{name}-r{k}", parent / f"{name}-r{k}"
        branch = name
        if _git(repo, "show-ref", "--verify", f"refs/heads/{branch}").returncode == 0:
            if fresh:
                branch = f"{branch}-{path.name.rsplit('-', 1)[-1]}"
            add = _git(repo, "worktree", "add", "-b", branch, str(path))
        else:
            add = _git(repo, "worktree", "add", "-b", branch, str(path), "HEAD")
        if add.returncode != 0:
            logger.warning("worktree add failed: %s", add.stderr.strip())
            return None
        return str(path)

    # ---------- the actions ----------

    def exec(
        self,
        dir: str,
        n: int,
        me: str,
        auto: bool = False,
        refine: bool = False,
        restart: bool = False,
    ) -> dict[str, Any]:
        store = self.store_of()
        todo = store.get(dir, n)
        if todo is None:
            return {"error": f"no todo #{n} in this list"}
        old = todo["status"]

        moved = store.update(
            dir, n, {"status": "drafting", "owner": me or None, "mode": "agent"}, me=me
        )
        if "error" in moved:
            return moved

        base = self.base_branch(dir)
        if restart:
            goal = protocol.restart_prompt(n, todo["title"], todo.get("prompt") or todo["title"], base)
            wt = self.worktree(dir, n, fresh=True)
        else:
            drafts = None if refine else todo.get("drafts")
            if refine:
                todo["prompt"] = todo.get("prompt") or todo["title"]
            goal = protocol.exec_prompt(
                n, todo["title"], base, drafts, auto=auto, refine=refine
            )
            wt = self.worktree(dir, n)
        if wt is None:
            # No repo to work in: the worker runs where the session is.
            wt = dir or None

        try:
            handle = self.api.launch(goal, wt, correlation_id(dir, n))
        except Exception as exc:
            # No active turn (idle session) or the launch was refused: hand
            # the same protocol to the session's own agent as a queued turn.
            logger.info("launch refused (#%s): %s", n, exc)
            if self.api.inject(goal):
                return {
                    "ok": True,
                    "n": n,
                    "injected": True,
                    "note": "worker launched in the chat's next turn",
                }
            store.update(dir, n, {"status": old, "owner": todo.get("owner")}, me=me)
            return {"error": f"couldn't launch a worker for #{n}: {exc}"}

        store.update(
            dir,
            n,
            {
                "status": "running",
                "task_id": str(handle.get("subagent_id", "")) or None,
                "handle": handle,
                "branch": f"{Path(wt).name}" if wt else None,
                "owner": me or None,
            },
            me=me,
        )
        return {"ok": True, "n": n, "worktree": wt, "launched": True}

    def _cancel_run(self, dir: str, n: int, me: str, to: str) -> dict[str, Any]:
        store = self.store_of()
        todo = store.get(dir, n)
        if todo is None:
            return {"error": f"no todo #{n} in this list"}
        if todo.get("handle"):
            self.api.cancel(todo["handle"], reason=f"todo #{n} {to} by its owner")
        res = store.update(
            dir,
            n,
            {
                "status": to,
                "summary": f"{to} by its owner; the run and branch are kept"
                if to == "stopped"
                else f"paused; the run is kept to resume",
                "owner": me or None,
            },
            me=me,
        )
        return res if "error" in res else {"ok": True, "n": n, "status": to}

    def stop(self, dir: str, n: int, me: str) -> dict[str, Any]:
        return self._cancel_run(dir, n, me, "stopped")

    def pause(self, dir: str, n: int, me: str) -> dict[str, Any]:
        return self._cancel_run(dir, n, me, "paused")

    def resume(self, dir: str, n: int, me: str) -> dict[str, Any]:
        """A fresh worker on the run the todo kept, briefed with its calls.
        With nothing kept it starts as Exec would."""
        store = self.store_of()
        todo = store.get(dir, n)
        if todo is None:
            return {"error": f"no todo #{n} in this list"}
        if not todo.get("task_id") and not todo.get("branch"):
            return self.exec(dir, n, me, auto=True)

        base = self.base_branch(dir)
        digest = ""
        for agent in store.agents_for(dir, n):
            calls = store.calls_for(agent["child_session_id"])
            if calls:
                digest = protocol.calls_digest(calls)
        goal = protocol.resume_prompt(n, todo["title"], base, digest)
        wt = self.worktree(dir, n) or dir or None

        moved = store.update(
            dir, n, {"status": "drafting", "owner": me or None}, me=me
        )
        if "error" in moved:
            return moved
        try:
            handle = self.api.launch(goal, wt, correlation_id(dir, n))
        except Exception as exc:
            if self.api.inject(goal):
                return {"ok": True, "n": n, "injected": True}
            store.update(dir, n, {"status": todo["status"], "owner": todo.get("owner")}, me=me)
            return {"error": f"couldn't resume #{n}: {exc}"}
        store.update(
            dir,
            n,
            {
                "status": "running",
                "task_id": str(handle.get("subagent_id", "")) or todo.get("task_id"),
                "handle": handle,
                "owner": me or None,
            },
            me=me,
        )
        return {"ok": True, "n": n, "resumed": True, "worktree": wt}
