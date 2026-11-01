"""What a todo may do next: the chips its row offers — which the /todo
verbs share, so a chip and its command can't disagree — and the status
moves the transitions table allows (which every status write passes).

Ported from claude-todo's hooks/moves.ts and hooks/marks.tsx CHIP table.
"""

from __future__ import annotations

from typing import Any

INFLIGHT = {"drafting", "planning", "running", "paused"}


def is_owned_elsewhere(todo: dict[str, Any], me: str) -> bool:
    """In flight in a session other than `me`. A todo paused before it ever
    ran has no run to take, so any session may start it."""
    return (
        todo["status"] in INFLIGHT
        and todo.get("owner") not in (None, "", me)
        and not (todo["status"] == "paused" and not todo["has_run"])
    )


def can_resume(todo: dict[str, Any]) -> bool:
    """What Resume is offered for: a paused todo, or a stopped or failed one
    with a run to pick up (a child session or a kept branch)."""
    if todo["status"] == "paused":
        return True
    if todo["status"] in ("stopped", "failed"):
        return bool(todo.get("task_id") or todo.get("branch"))
    return False


# The chips a todo's row offers, each a /todo verb of its own name. Order:
# carry on, start over, put away or destroy, Details last. A chip is
# offered only where it can act.
Chip = str


def offers(todo: dict[str, Any], me: str, just_stopped: tuple[int, ...] = ()) -> list[Chip]:
    if is_owned_elsewhere(todo, me):
        return ["takeover", "details"]
    status = todo["status"]
    if status in ("prompting", "pending"):
        chips: list[Chip] = []
        if status == "pending":
            chips += ["exec", "auto"]
        chips += ["rename"]
        if not todo["has_run"]:
            chips += ["delete"]
        chips += ["details"]
        return chips
    if status in ("drafting", "planning"):
        return ["reset"]
    if status == "running":
        return ["pause", "stop", "details"]
    if status == "paused":
        return ["resume", "stop", "details"]
    if status in ("stopped", "failed"):
        chips = []
        if can_resume(todo):
            chips.append("resume")
        chips += ["refine", "restart"]
        if todo["n"] in just_stopped:
            chips.append("leave")
        chips.append("details")
        return chips
    if status == "done":
        return ["details"]
    return []


# Every chip's symbol and what it does (claude-todo's hooks/marks.tsx CHIP
# table). The pane draws the symbol; /todo help and the hover labels say
# the does.
CHIP: dict[str, dict[str, str]] = {
    "exec": {"symbol": "▶", "does": "run it, picking or writing its prompt first"},
    "auto": {"symbol": "⚡", "does": "run it on its first draft, asking nothing"},
    "fire": {"symbol": "🔥", "does": "auto-run the lowest-numbered pending todo"},
    "rename": {"symbol": "✎", "does": "give the todo a new title"},
    "refine": {"symbol": "✨", "does": "rework its prompt in the chat, then run it"},
    "restart": {"symbol": "↻", "does": "run it again from a fresh start"},
    "resume": {"symbol": "⏵", "does": "pick its run up where it left off"},
    "pause": {"symbol": "⏸", "does": "halt it, keeping its run to resume"},
    "stop": {"symbol": "■", "does": "execution halts; the todo is kept"},
    "delete": {"symbol": "✕", "does": "remove the todo for good"},
    "takeover": {"symbol": "⇄", "does": "it's marked stopped here; its run in the other session isn't stopped"},
    "details": {"symbol": "☰", "does": "browse its prompt, subtasks and calls"},
    "reset": {"symbol": "↺", "does": "take it back from the chat"},
    "keep": {"symbol": "↩", "does": "leave it as it is"},
    "cancel": {"symbol": "↩", "does": "leave it as it is"},
    "back": {"symbol": "↩", "does": "back to the list"},
    "leave": {"symbol": "✓", "does": "fold it away as it is"},
}

# The verbs the /todo dispatcher knows: every chip has one of its own name.
VERBS = (
    "add", "list", "help",
    "exec", "auto", "fire", "rename", "delete", "reset", "pause", "stop",
    "resume", "refine", "restart", "leave", "takeover", "details", "show",
)


def refusal(todo: dict[str, Any], me: str, verb: str) -> str:
    """What a refused verb says: the todo, its status, and the verbs that do
    apply on it (C26)."""
    can = [v for v in offers(todo, me) if v != "details"]
    where = f"#{todo['n']} \"{todo['title']}\""
    if not can:
        return f"{where} is {todo['status']}: no action applies to it here."
    return f"{where} is {todo['status']}: /todo {verb} doesn't apply; try /todo {can[0]}." \
        if len(can) == 1 else \
        f"{where} is {todo['status']}: /todo {verb} doesn't apply; try one of: " + ", ".join(f"/todo {v}" for v in can) + "."


_MARKS = {
    "prompting": "◔", "pending": "●", "drafting": "✎", "planning": "◇",
    "running": "▶", "paused": "⏸", "stopped": "■", "done": "✓", "failed": "✗",
}
_SPIN = ("·", "✢", "✳", "✶", "✻", "✽")


def status_glyph(status: str, frame: int = 0) -> str:
    """The mark a status shows; running spins (SPIN, claude-todo's frames)."""
    if status == "running":
        return _SPIN[frame % len(_SPIN)]
    return _MARKS.get(status, "·")
