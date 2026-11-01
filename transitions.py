"""The one table of moves a todo may make, and the guard that reads it.

Every status write goes through ``Store.update`` (db.py), which refuses a
move the table doesn't allow and says which moves it does. Python imports
JSON directly, so unlike claude-todo there is no mirror module to
regenerate; ``transitions.json`` is the only source.

``[*]`` is before the todo exists (``from``) or after it is removed (``to``).
A write that keeps the status is never a move, so the guard always allows it.
"""

from __future__ import annotations

import json
from pathlib import Path

_TABLE_PATH = Path(__file__).resolve().parent / "transitions.json"
TABLE: list[dict] = json.loads(_TABLE_PATH.read_text(encoding="utf-8"))["transitions"]

STATUSES = (
    "prompting",
    "pending",
    "drafting",
    "planning",
    "running",
    "paused",
    "stopped",
    "done",
    "failed",
)
ENDS = ("[*]",) + STATUSES


def can_move(from_: str, to: str) -> bool:
    """Whether the table allows a todo to go ``from_`` → ``to``. A write that
    keeps the status isn't a move, so it always may."""
    if from_ == to:
        return True
    return any(m["from"] == from_ and m["to"] == to for m in TABLE)


def moves_from(from_: str) -> list[str]:
    """Where a todo in ``from_`` may go, in the table's order, without repeats."""
    seen: list[str] = []
    for m in TABLE:
        if m["from"] == from_ and m["to"] != from_ and m["to"] not in seen:
            seen.append(m["to"])
    return seen


def _shown(s: str) -> str:
    return "removed" if s == "[*]" else s


def move_refusal(n: int, from_: str, to: str) -> str:
    """What a refused move says: the move, and the moves the table does allow."""
    can = [_shown(s) for s in moves_from(from_)]
    return (
        f"#{n} can't go from {_shown(from_)} to {_shown(to)}: "
        f"from {_shown(from_)} it can only go to {', '.join(can) if can else 'nowhere'}."
    )
