"""The SQLite store: one database per Hermes profile (``plugin_db``), lists
keyed by the repo the session is in, WAL, and a revision guard so concurrent
sessions never lose each other's edits.

Every todo write carries the list's revision it was computed from:

    BEGIN IMMEDIATE · bump rev where rev = R · write rows · COMMIT

If another session wrote first, the guarded UPDATE matches no row, the
transaction rolls back, the list is re-read and the same edit is applied
again, up to five tries. Agents and calls are keyed by id and only ever
written by the session that recorded them, so they are written through,
with no revision check.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import Any, Callable

from . import transitions

# Columns of the todos table the tools may set through Store.update.
_TODO_FIELDS = (
    "title",
    "prompt",
    "mode",
    "task_id",
    "handle",
    "run_id",
    "summary",
    "branch",
    "landed",
    "owner",
    "draft_key",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS lists (
  dir  TEXT PRIMARY KEY,
  rev  INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS todos (
  dir        TEXT    NOT NULL,
  n          INTEGER NOT NULL,
  title      TEXT    NOT NULL,
  status     TEXT    NOT NULL,
  prompt     TEXT,
  mode       TEXT,
  task_id    TEXT,
  handle     TEXT,
  run_id     TEXT,
  summary    TEXT,
  branch     TEXT,
  landed     TEXT,
  done_at    REAL,
  has_run    INTEGER NOT NULL DEFAULT 0,
  owner      TEXT,
  drafts     TEXT,
  draft_key  TEXT,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  PRIMARY KEY (dir, n)
);
CREATE INDEX IF NOT EXISTS todos_dir ON todos (dir);
CREATE TABLE IF NOT EXISTS agents (
  child_session_id TEXT PRIMARY KEY,
  dir      TEXT NOT NULL,
  n        INTEGER NOT NULL,
  kind     TEXT NOT NULL DEFAULT 'agent',
  status   TEXT NOT NULL DEFAULT 'running',
  started_at REAL NOT NULL,
  ended_at  REAL,
  summary   TEXT
);
CREATE INDEX IF NOT EXISTS agents_todo ON agents (dir, n);
CREATE TABLE IF NOT EXISTS calls (
  id         TEXT PRIMARY KEY,
  agent_id   TEXT NOT NULL,
  tool       TEXT NOT NULL,
  args       TEXT,
  result     TEXT,
  state      TEXT NOT NULL,
  ms         REAL,
  started_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS calls_agent ON calls (agent_id, started_at);
CREATE TABLE IF NOT EXISTS session_ends (
  session_id TEXT PRIMARY KEY,
  ended_at   REAL NOT NULL
);
"""

# Columns added after 0.1.0, migrated in place for an existing database.
_MIGRATIONS = (
    ("todos", "handle", "TEXT"),
    ("todos", "draft_key", "TEXT"),
)


class Conflict(Exception):
    """Another session wrote the list first; the write is retried."""


class GiveUp(Exception):
    """Another session kept beating the write for ``tries`` tries."""

    def __init__(self, tries: int):
        super().__init__(f"another session beat this write {tries} times")
        self.tries = tries


def _row_to_todo(row: sqlite3.Row) -> dict[str, Any]:
    todo = dict(row)
    todo["has_run"] = bool(todo["has_run"])
    if todo.get("drafts"):
        try:
            todo["drafts"] = json.loads(todo["drafts"])
        except (ValueError, TypeError):
            todo["drafts"] = None
    else:
        todo["drafts"] = None
    if todo.get("handle"):
        try:
            todo["handle"] = json.loads(todo["handle"])
        except (ValueError, TypeError):
            todo["handle"] = None
    return todo


def _public(todo: dict[str, Any]) -> dict[str, Any]:
    """The fields a todo answer or the pane's route shows."""
    return {
        k: todo.get(k)
        for k in (
            "n", "title", "status", "prompt", "mode", "task_id", "run_id",
            "summary", "branch", "landed", "has_run", "owner", "drafts",
        )
    }


class Store:
    """A guarded, WAL-backed todo store over one SQLite connection."""

    def __init__(self, conn: sqlite3.Connection, tries: int = 5):
        self.conn = conn
        self.tries = tries
        conn.row_factory = sqlite3.Row
        try:
            conn.isolation_level = None  # explicit BEGIN IMMEDIATE below
        except sqlite3.ProgrammingError:  # pragma: no cover - closed conn
            raise
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.executescript(_SCHEMA)
        for table, column, decl in _MIGRATIONS:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
            except sqlite3.OperationalError:
                pass  # already there

    # ---------- the guarded write ----------

    def write(self, dir: str, op: Callable[[sqlite3.Connection, int], Any]) -> Any:
        """Run ``op(conn, new_rev)`` inside one rev-guarded transaction.

        ``op`` returns its result (or raises). A conflict with another
        session rolls back and retries on the fresh revision.
        """
        for _ in range(self.tries):
            rev = self.rev(dir)
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                bumped = self.conn.execute(
                    "UPDATE lists SET rev = rev + 1 WHERE dir = ? AND rev = ?",
                    (dir, rev),
                )
                if bumped.rowcount != 1:
                    raise Conflict()
                result = op(self.conn, rev + 1)
                self.conn.execute("COMMIT")
                return result
            except Conflict:
                self.conn.execute("ROLLBACK")
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
        raise GiveUp(self.tries)

    def rev(self, dir: str) -> int:
        """The list's revision, creating the list row on first sight."""
        row = self.conn.execute(
            "SELECT rev FROM lists WHERE dir = ?", (dir,)
        ).fetchone()
        if row is not None:
            return row["rev"]
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self.conn.execute("INSERT OR IGNORE INTO lists (dir, rev) VALUES (?, 0)", (dir,))
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        row = self.conn.execute("SELECT rev FROM lists WHERE dir = ?", (dir,)).fetchone()
        return row["rev"]

    @property
    def path(self) -> str | None:
        """The database file's path, for reopening it on another thread."""
        try:
            row = self.conn.execute("PRAGMA database_list").fetchone()
            return row["file"] if row and row["file"] else None
        except sqlite3.Error:
            return None

    def reopen(self) -> "Store | None":
        """A fresh Store on the same file, for a thread of our own."""
        path = self.path
        if not path:
            return None
        return Store(sqlite3.connect(path), tries=self.tries)

    # ---------- reads ----------

    def todos(self, dir: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM todos WHERE dir = ? ORDER BY n", (dir,)
        ).fetchall()
        return [_row_to_todo(r) for r in rows]

    def get(self, dir: str, n: int) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM todos WHERE dir = ? AND n = ?", (dir, n)
        ).fetchone()
        return _row_to_todo(row) if row is not None else None

    # ---------- writes ----------

    def add(self, dir: str, title: str) -> int:
        """Add a todo, pending, as the next number in its list."""
        title = title.strip()
        if not title:
            raise ValueError("a todo needs a title")

        def op(conn: sqlite3.Connection, rev: int) -> int:
            row = conn.execute(
                "SELECT COALESCE(MAX(n), 0) + 1 AS n FROM todos WHERE dir = ?", (dir,)
            ).fetchone()
            n = row["n"]
            now = time.time()
            conn.execute(
                "INSERT INTO todos (dir, n, title, status, created_at, updated_at)"
                " VALUES (?, ?, ?, 'pending', ?, ?)",
                (dir, n, title, now, now),
            )
            return n

        return self.write(dir, op)

    def update(
        self, dir: str, n: int, changes: dict[str, Any], me: str = ""
    ) -> dict[str, Any]:
        """Change a todo through the one guard every status write passes.

        A status move the transitions table doesn't allow writes nothing —
        not the other fields either — and answers with the refusal naming
        the moves that are allowed.
        """
        bad = set(changes) - set(_TODO_FIELDS) - {"status"}
        if bad:
            raise ValueError(f"unknown todo fields: {sorted(bad)}")

        def op(conn: sqlite3.Connection, rev: int) -> dict[str, Any]:
            row = conn.execute(
                "SELECT * FROM todos WHERE dir = ? AND n = ?", (dir, n)
            ).fetchone()
            if row is None:
                return {"error": f"no todo #{n} in this list"}

            old_status = row["status"]
            new_status = changes.get("status", old_status)
            if new_status != old_status and not transitions.can_move(old_status, new_status):
                return {"error": transitions.move_refusal(n, old_status, new_status)}

            sets: list[str] = []
            vals: list[Any] = []
            for field in _TODO_FIELDS:
                if field in changes:
                    sets.append(f"{field} = ?")
                    value = changes[field]
                    if field == "handle" and isinstance(value, dict):
                        value = json.dumps(value)
                    vals.append(value)
            if new_status != old_status:
                sets.append("status = ?")
                vals.append(new_status)
                if new_status == "running":
                    sets.append("has_run = 1")
                if new_status == "done" and row["done_at"] is None:
                    sets.append("done_at = ?")
                    vals.append(time.time())
            if sets:
                sets.append("updated_at = ?")
                vals.append(time.time())
                vals.extend([dir, n])
                conn.execute(
                    f"UPDATE todos SET {', '.join(sets)} WHERE dir = ? AND n = ?", vals
                )
            todo = _row_to_todo(
                conn.execute(
                    "SELECT * FROM todos WHERE dir = ? AND n = ?", (dir, n)
                ).fetchone()
            )
            return {"ok": True, "todo": _public(todo), "me": me, "rev": rev}

        return self.write(dir, op)

    def delete(self, dir: str, n: int) -> dict[str, Any]:
        """Remove a todo, only where the table allows ([*] rows: prompting
        or pending). A todo that has run keeps its record."""

        def op(conn: sqlite3.Connection, rev: int) -> dict[str, Any]:
            row = conn.execute(
                "SELECT status FROM todos WHERE dir = ? AND n = ?", (dir, n)
            ).fetchone()
            if row is None:
                return {"error": f"no todo #{n} in this list"}
            if not transitions.can_move(row["status"], "[*]"):
                return {"error": transitions.move_refusal(n, row["status"], "[*]")}
            conn.execute("DELETE FROM todos WHERE dir = ? AND n = ?", (dir, n))
            return {"ok": True, "deleted": n}

        return self.write(dir, op)

    def expire(self, dir: str, ttl_minutes: float) -> list[int]:
        """Remove done todos past their keep period (done → [*] by expires);
        a ttl of 0 keeps them. Returns the numbers removed."""
        if ttl_minutes <= 0:
            return []

        def op(conn: sqlite3.Connection, rev: int) -> list[int]:
            rows = conn.execute(
                "SELECT n, done_at FROM todos WHERE dir = ? AND status = 'done'", (dir,)
            ).fetchall()
            gone = [
                r["n"]
                for r in rows
                if r["done_at"] is not None and time.time() - r["done_at"] >= ttl_minutes * 60
            ]
            for n in gone:
                conn.execute("DELETE FROM todos WHERE dir = ? AND n = ?", (dir, n))
            return gone

        return self.write(dir, op)

    # ---------- the drafter (phase 2) ----------

    def start_drafting(self, dir: str, n: int) -> str | None:
        """Turn a pending or prompting todo prompting under a fresh draft
        key (a rename relaunches the drafter this way); the key is what
        makes a late drafter's answer stale."""
        key = f"{n}:{time.time_ns()}"

        def op(conn: sqlite3.Connection, rev: int) -> str | None:
            row = conn.execute(
                "SELECT status FROM todos WHERE dir = ? AND n = ?", (dir, n)
            ).fetchone()
            if row is None:
                return None
            if not transitions.can_move(row["status"], "prompting"):
                return None
            conn.execute(
                "UPDATE todos SET status = 'prompting', draft_key = ?, drafts = NULL,"
                " updated_at = ? WHERE dir = ? AND n = ?",
                (key, time.time(), dir, n),
            )
            return key

        return self.write(dir, op)

    def set_drafts(
        self, dir: str, n: int, key: str, drafts: list[dict[str, str]]
    ) -> dict[str, Any]:
        """Store a drafter's answer under its own launch's key. An older
        key's drafts are stale and refused. Storing them turns a prompting
        todo pending; a todo that moved on keeps its status."""

        clean = [
            {"label": str(d.get("label", ""))[:64], "prompt": str(d.get("prompt", ""))}
            for d in drafts
            if d.get("label") and d.get("prompt")
        ][:3]

        def op(conn: sqlite3.Connection, rev: int) -> dict[str, Any]:
            row = conn.execute(
                "SELECT status, draft_key FROM todos WHERE dir = ? AND n = ?", (dir, n)
            ).fetchone()
            if row is None:
                return {"stale": True, "error": f"no todo #{n} in this list"}
            if row["draft_key"] != key:
                return {"stale": True}  # a rename relaunched the drafter
            sets = "drafts = ?, updated_at = ?"
            vals: list[Any] = [json.dumps(clean), time.time()]
            if row["status"] == "prompting":
                sets += ", status = 'pending'"
            conn.execute(
                f"UPDATE todos SET {sets} WHERE dir = ? AND n = ?", [*vals, dir, n]
            )
            return {"ok": True, "n": n, "drafts": len(clean)}

        return self.write(dir, op)

    # ---------- agents, calls and sessions (phases 3-4) ----------

    def link_agent(
        self, dir: str, n: int, child_session_id: str, kind: str = "agent"
    ) -> None:
        """Record that a child session works for todo #n (written through)."""
        self.conn.execute(
            "INSERT OR REPLACE INTO agents (child_session_id, dir, n, kind, status, started_at)"
            " VALUES (?, ?, ?, ?, 'running', ?)",
            (child_session_id, dir, n, kind, time.time()),
        )

    def agent_of(self, child_session_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM agents WHERE child_session_id = ?", (child_session_id,)
        ).fetchone()
        return dict(row) if row is not None else None

    def agents_for(self, dir: str, n: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM agents WHERE dir = ? AND n = ? ORDER BY started_at", (dir, n)
        ).fetchall()
        return [dict(r) for r in rows]

    def end_agent(self, child_session_id: str, status: str, summary: str | None) -> None:
        self.conn.execute(
            "UPDATE agents SET status = ?, ended_at = ?, summary = ? WHERE child_session_id = ?",
            (status, time.time(), (summary or "")[:2000] or None, child_session_id),
        )

    def record_call(
        self,
        call_id: str,
        agent_id: str,
        tool: str,
        args: str | None,
        result: str | None,
        state: str,
        ms: float | None,
    ) -> None:
        """A child's tool call, with its input and response (written through)."""
        self.conn.execute(
            "INSERT OR REPLACE INTO calls (id, agent_id, tool, args, result, state, ms, started_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                call_id or f"{agent_id}:{time.time_ns()}",
                agent_id,
                tool,
                (args or "")[:8000] or None,
                (result or "")[:12000] or None,
                state,
                ms,
                time.time(),
            ),
        )

    def calls_for(self, agent_id: str, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM calls WHERE agent_id = ? ORDER BY started_at DESC LIMIT ?",
            (agent_id, limit),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def note_session_end(self, session_id: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO session_ends (session_id, ended_at) VALUES (?, ?)",
            (session_id, time.time()),
        )

    def ended_sessions(self) -> set[str]:
        rows = self.conn.execute("SELECT session_id FROM session_ends").fetchall()
        return {r["session_id"] for r in rows}

    def reclaim_stale(self, dir: str, me: str) -> list[int]:
        """Take back todos whose owner session has ended (the kanban-style
        stale reclaim): each goes stopped, keeping its run and worktree."""

        def op(conn: sqlite3.Connection, rev: int) -> list[int]:
            rows = conn.execute(
                "SELECT n, status, owner FROM todos WHERE dir = ?", (dir,)
            ).fetchall()
            gone: list[int] = []
            for r in rows:
                if r["status"] not in ("drafting", "planning", "running", "paused"):
                    continue
                owner = r["owner"]
                if not owner or owner == me:
                    continue
                ended = conn.execute(
                    "SELECT 1 FROM session_ends WHERE session_id = ?", (owner,)
                ).fetchone()
                if ended is None:
                    continue
                if not transitions.can_move(r["status"], "stopped"):
                    continue
                conn.execute(
                    "UPDATE todos SET status = 'stopped',"
                    " summary = COALESCE(summary, 'its session ended; the run is kept'),"
                    " updated_at = ? WHERE dir = ? AND n = ?",
                    (time.time(), dir, r["n"]),
                )
                gone.append(r["n"])
            return gone

        return self.write(dir, op)


def done_ttl_minutes(default: float = 5.0) -> float:
    """A done todo's lifetime in minutes; HERMES_TODO_DONE_TTL_MINUTES
    overrides, and 0 keeps done todos."""
    raw = os.environ.get("HERMES_TODO_DONE_TTL_MINUTES")
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default
