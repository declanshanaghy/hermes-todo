"""The /todo dispatcher: every chip has a verb of its own name, and a verb
works exactly where its chip is drawn — both read the same offers(), so
they can't drift. Anywhere else it answers in one line with the todo's
status and the verbs that do apply, and changes nothing.

A typed verb acts at once, without the pane's confirmation question:
typing it is already the explicit request.
"""

from __future__ import annotations

from typing import Any, Callable

from . import drafter, moves
from .db import done_ttl_minutes


class Dispatcher:
    """Handles /todo <verb> [<n|title> …] for one running Hermes."""

    def __init__(
        self,
        store_of: Callable[[], Any],
        runner_of: Callable[[], Any],
        dir_of: Callable[[], str],
    ):
        self.store_of = store_of
        self.runner_of = runner_of
        self.dir_of = dir_of
        self.just_stopped: set[int] = set()

    # ---------- the entry point ----------

    def __call__(self, raw: str, **kwargs: Any) -> str:
        raw = (raw or "").strip()
        if not raw:
            return self._usage()
        me = str(kwargs.get("session_id") or "")
        verb, _, rest = raw.partition(" ")
        verb = verb.strip().lower()
        if verb in ("help", "?"):
            return self.help()
        if verb not in moves.VERBS:
            return self.add(raw)  # a first word that's no verb adds a todo
        rest = rest.strip()
        if verb == "add":
            return self.add(rest)
        if verb in ("list", "ls"):
            return self.list_todos()
        if verb == "show":
            return "The pane opens with the TUI widget installed (see docs/hermes-orchestration.md); /todo list answers here."
        if verb == "fire":
            return self.fire(me)
        if verb == "rename":
            target, _, title = rest.partition(" ")
            return self.rename(target.strip(), title.strip())
        return self.act(verb, rest, me)

    # ---------- pieces ----------

    def _usage(self) -> str:
        return 'Usage: /todo <verb> [<n|#n|title fragment>] — try /todo help, /todo add <title>, /todo list.'

    def add(self, title: str) -> str:
        from . import tools

        if not title:
            return "Usage: /todo add <title>"
        store = self.store_of()
        try:
            n = store.add(self.dir_of(), title)
        except Exception as exc:
            return f"add failed: {exc}"
        if tools.LLM is not None:
            key = store.start_drafting(self.dir_of(), n)
            if key:
                others = [t["title"] for t in store.todos(self.dir_of()) if t["n"] != n]
                drafter.schedule(self.dir_of(), n, title, key, others)
                return f"● #{n} added — drafting its prompts, ready in a moment"
        return f"● #{n} added — /todo exec drafts its prompt when you run it"

    def list_todos(self) -> str:
        store = self.store_of()
        store.expire(self.dir_of(), done_ttl_minutes())
        todos = store.todos(self.dir_of())
        if not todos:
            return "No todos in this repo yet — /todo add <title>."
        counts: dict[str, int] = {}
        lines = []
        for t in todos:
            counts[t["status"]] = counts.get(t["status"], 0) + 1
        summary = " ".join(
            f"{moves.status_glyph(s)}{counts[s]}" for s in moves._MARKS if s in counts
        )
        for t in todos:
            glyph = moves.status_glyph(t["status"])
            line = f"{glyph} #{t['n']} {t['title']}"
            if t.get("summary"):
                line += f" — {t['summary'][:60]}"
            lines.append(line)
        return f"{len(todos)} Todos {summary}\n" + "\n".join(lines)

    def help(self) -> str:
        rows = [f"/todo {v}: {d['does']}" for v, d in moves.CHIP.items()]
        return "\n".join(
            [
                "/todo add <title>: queue a todo for this repo",
                "/todo list: the backlog with counts",
                *rows,
                "/todo fire: auto-run the lowest-numbered pending todo",
                "/todo <title>: adds it (any first word that is no verb)",
            ]
        )

    def find(self, target: str) -> Any | None:
        """A todo by number, #n, or a case-insensitive title fragment."""
        store = self.store_of()
        todos = store.todos(self.dir_of())
        clean = target.lstrip("#").strip().lower()
        if not clean:
            return None
        for t in todos:
            if str(t["n"]) == clean:
                return t
        for t in todos:
            if clean in t["title"].lower():
                return t
        return None

    def fire(self, me: str = "") -> str:
        store = self.store_of()
        todos = store.todos(self.dir_of())
        pending = [t for t in todos if t["status"] == "pending"]
        if not pending:
            ready = [t for t in todos if t["status"] == "prompting"]
            if ready:
                return "Nothing is pending yet: " + ", ".join(
                    f"#{t['n']}" for t in ready
                ) + " still drafting their prompts."
            return "Nothing to fire: no pending todo in this repo."
        t = min(pending, key=lambda t: t["n"])
        return f"🔥 firing #{t['n']} — " + self.act("auto", str(t["n"]), me)

    def rename(self, target: str, title: str) -> str:
        store = self.store_of()
        todo = self.find(target)
        if todo is None:
            return f"No todo matches \"{target}\"."
        if not title:
            return "Usage: /todo rename <n|title> <new title>"
        if title == todo["title"]:
            return f"#{todo['n']} is already called that."
        res = store.update(self.dir_of(), todo["n"], {"title": title})
        if "error" in res:
            return res["error"]
        # A rename clears the drafts and drafts again (R5): an old
        # drafter's late answer is stale under the new key.
        from . import tools

        if tools.LLM is not None:
            key = store.start_drafting(self.dir_of(), todo["n"])
            if key:
                others = [t["title"] for t in store.todos(self.dir_of()) if t["n"] != todo["n"]]
                drafter.schedule(self.dir_of(), todo["n"], title, key, others)
                return f"✎ #{todo['n']} renamed — drafting prompts for it again"
        return f"✎ #{todo['n']} renamed"

    def act(self, verb: str, target: str, me: str = "") -> str:
        store = self.store_of()
        todo = self.find(target)
        if todo is None:
            return f"No todo matches \"{target}\"." if target else self._usage()
        n = todo["n"]
        offered = moves.offers(todo, me, tuple(self.just_stopped))
        # Exec and Auto keep their wider reach: a stopped or failed todo too.
        allowed = verb in offered or (
            verb in ("exec", "auto") and todo["status"] in ("stopped", "failed")
        )
        if not allowed:
            return moves.refusal(todo, me, verb)

        runner = self.runner_of()
        dir = self.dir_of()
        if verb in ("exec", "auto"):
            res = runner.exec(dir, n, me, auto=(verb == "auto"))
        elif verb == "resume":
            res = runner.resume(dir, n, me)
        elif verb == "pause":
            res = runner.pause(dir, n, me)
        elif verb == "stop":
            res = runner.stop(dir, n, me)
            if isinstance(res, dict) and res.get("ok"):
                self.just_stopped.add(n)
        elif verb == "refine":
            res = runner.exec(dir, n, me, refine=True)
        elif verb == "restart":
            res = runner.exec(dir, n, me, restart=True)
        elif verb == "reset":
            to = "stopped" if todo["has_run"] else "pending"
            res = store.update(dir, n, {"status": to, "owner": None}, me=me)
        elif verb == "delete":
            res = store.delete(dir, n)
        elif verb == "leave":
            self.just_stopped.discard(n)
            return f"✓ #{n} folded away"
        elif verb == "takeover":
            res = store.update(
                dir, n, {"status": "stopped", "task_id": None, "handle": None,
                          "run_id": None, "owner": me or None, "summary": "taken over here; its run there isn't stopped"},
                me=me,
            )
        elif verb == "details":
            return self.details(todo)
        else:  # pragma: no cover - VERBS gates it
            return self._usage()

        if isinstance(res, dict) and res.get("error"):
            return res["error"]
        mark = moves.status_glyph(
            store.get(dir, n)["status"] if store.get(dir, n) else todo["status"]
        )
        what = {
            "exec": "started", "auto": "started (auto)", "resume": "resumed",
            "pause": "paused", "stop": "stopped", "refine": "refining",
            "restart": "restarted", "reset": "reset", "delete": "removed",
            "takeover": "taken over here",
        }.get(verb, "done")
        if verb == "delete":
            return f"✕ #{n} removed"
        if isinstance(res, dict) and res.get("injected"):
            return f"{mark} #{n} {what} — the protocol goes in on your next turn"
        return f"{mark} #{n} {what}"

    def details(self, todo: Any) -> str:
        store = self.store_of()
        dir = self.dir_of()
        n = todo["n"]
        agents = store.agents_for(dir, n)
        calls = sum(len(store.calls_for(a["child_session_id"], limit=500)) for a in agents)
        lines = [f"☰ #{n} \"{todo['title']}\" — {todo['status']}"]
        if todo.get("prompt"):
            lines.append(f"prompt: {todo['prompt'][:200]}")
        if todo.get("summary"):
            lines.append(f"summary: {todo['summary']}")
        if todo.get("branch"):
            lines.append(f"branch: {todo['branch']}" + (f", landed {todo['landed']}" if todo.get("landed") else ""))
        if todo.get("drafts"):
            lines.append(f"{len(todo['drafts'])} drafts ready: " + "; ".join(d["label"] for d in todo["drafts"]))
        lines.append(f"{len(agents)} worker(s), {calls} recorded call(s)")
        return "\n".join(lines)
