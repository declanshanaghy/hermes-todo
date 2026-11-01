"""The transitions table and its guard: the moves the code may make are
exactly the table's, and a move it doesn't allow is refused naming the
moves that are."""

import pytest

from hermes_todo import transitions


def _allowed_pairs():
    return {(m["from"], m["to"]) for m in transitions.TABLE}


def test_every_table_move_is_between_known_ends():
    for m in transitions.TABLE:
        assert m["from"] in transitions.ENDS, m
        assert m["to"] in transitions.ENDS, m


def test_can_move_allows_exactly_the_table_for_every_pair():
    allowed = _allowed_pairs()
    for from_ in transitions.ENDS:
        for to in transitions.ENDS:
            assert transitions.can_move(from_, to) == (
                from_ == to or (from_, to) in allowed
            ), (from_, to)


def test_moves_from_lists_each_target_once_in_table_order():
    for from_ in transitions.ENDS:
        targets = transitions.moves_from(from_)
        assert len(targets) == len(set(targets))
        assert all((from_, t) in _allowed_pairs() for t in targets)
        table_order = [m["to"] for m in transitions.TABLE if m["from"] == from_ and m["to"] != from_]
        assert targets == list(dict.fromkeys(table_order))


def test_a_refusal_names_the_todo_and_the_moves_allowed():
    msg = transitions.move_refusal(3, "pending", "done")
    assert msg.startswith("#3 can't go from pending to done")
    assert "drafting" in msg  # the move the table does allow


def test_the_life_the_docs_draw_is_walkable():
    # [*] → pending → drafting → running → done, and the pause/stop loop.
    for move in (
        ("[*]", "pending"),
        ("pending", "prompting"),
        ("prompting", "pending"),
        ("pending", "drafting"),
        ("drafting", "planning"),
        ("planning", "running"),
        ("drafting", "running"),
        ("running", "paused"),
        ("paused", "drafting"),
        ("running", "stopped"),
        ("paused", "stopped"),
        ("stopped", "drafting"),
        ("failed", "drafting"),
        ("running", "done"),
        ("running", "failed"),
        ("drafting", "pending"),
        ("done", "[*]"),
        ("prompting", "[*]"),
    ):
        assert transitions.can_move(*move), move


def test_moves_the_docs_forbid_are_refused():
    for move in (
        ("pending", "running"),  # never straight to running
        ("pending", "done"),
        ("done", "drafting"),  # done only leaves by expiry
        ("stopped", "pending"),
        ("failed", "pending"),
        ("running", "drafting"),
        ("done", "failed"),
        ("[*]", "running"),
    ):
        assert not transitions.can_move(*move), move


def test_moves_from_before_the_todo_exists():
    assert transitions.moves_from("[*]") == ["pending"]
    assert "pending" in transitions.move_refusal(1, "[*]", "running")
