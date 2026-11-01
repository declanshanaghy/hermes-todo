"""The SQLite store: one database per Hermes profile (``plugin_db``), lists
keyed by the repo the session is in, WAL, and a revision guard so concurrent
sessions never lose each other's edits.

Every todo write carries the list's revision it was computed from:

    BEGIN IMMEDIATE · bump rev where rev = R · write rows · COMMIT

If another session wrote first, the guarded UPDATE matches no row, the
transaction rolls back, the list is re-read and the same edit is applied
again, up to five tries. Agents, calls and turns (phase 4) are keyed by id
and only ever written by the session that recorded them, so they will need
no revision check.
"""

from __future__ import annotations

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
    "run_id",
    "summary",
    "branch",
    "landed",
    "owner",
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
  run_id     TEXT,
  summary    TEXT,
  branch     TEXT,
  landed     TEXT,
  done_at    REAL,
  has_run    INTEGER NOT NULL DEFAULT 0,
  owner      TEXT,
  drafts     TEXT,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  PRIMARY KEY (dir, n)
);
CREATE INDEX IF NOT EXISTS todos_dir ON todos (dir);
"""


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
    return todo


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
                    vals.append(changes[field])
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
            return {"ok": True, "todo": todo, "me": me, "rev": rev}

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
