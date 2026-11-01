"""What a todo may do next: the offers table (each state's chips) and the
verbs' gates — a chip and its /todo verb share it (claude-todo C24-C26,
V8)."""

from hermes_todo import moves


def _todo(status, **kw):
    base = {
        "n": 1, "title": "Fix login", "status": status, "has_run": False,
        "task_id": None, "branch": None, "owner": None,
    }
    base.update(kw)
    return base


def test_each_state_offers_exactly_its_chips():
    assert moves.offers(_todo("prompting"), "me") == ["rename", "delete", "details"]
    assert moves.offers(_todo("pending"), "me") == ["exec", "auto", "rename", "delete", "details"]
    assert moves.offers(_todo("pending", has_run=True), "me") == ["exec", "auto", "rename", "details"]
    assert moves.offers(_todo("drafting"), "me") == ["reset"]
    assert moves.offers(_todo("planning"), "me") == ["reset"]
    assert moves.offers(_todo("running"), "me") == ["pause", "stop", "details"]
    assert moves.offers(_todo("paused"), "me") == ["resume", "stop", "details"]
    assert moves.offers(_todo("stopped"), "me") == ["refine", "restart", "details"]
    assert moves.offers(_todo("stopped", task_id="t1"), "me") == ["resume", "refine", "restart", "details"]
    assert moves.offers(_todo("failed", branch="todo-1"), "me") == ["resume", "refine", "restart", "details"]
    assert moves.offers(_todo("stopped"), "me", just_stopped=(1,)) == ["refine", "restart", "leave", "details"]
    assert moves.offers(_todo("done"), "me") == ["details"]


def test_a_todo_in_flight_elsewhere_offers_only_takeover():
    away = moves.offers(_todo("running", owner="other"), "me")
    assert away == ["takeover", "details"]
    mine = moves.offers(_todo("running", owner="me"), "me")
    assert "pause" in mine and "takeover" not in mine


def test_a_todo_paused_before_it_ever_ran_belongs_to_no_session():
    assert not moves.is_owned_elsewhere(_todo("paused", owner="other"), "me")
    assert moves.is_owned_elsewhere(_todo("paused", owner="other", has_run=True), "me")


def test_a_refusal_names_the_status_and_the_verbs_that_apply():
    msg = moves.refusal(_todo("pending", n=3, title="Fix login"), "me", "pause")
    assert '#3 "Fix login" is pending' in msg
    assert "pause doesn't apply" in msg
    assert "/todo exec" in msg  # the verbs that do apply


def test_a_refusal_of_a_done_todo_says_no_action_applies():
    msg = moves.refusal(_todo("done", n=3, title="Fix login"), "me", "pause")
    assert "no action applies" in msg


def test_the_running_mark_spins():
    frames = [moves.status_glyph("running", f) for f in range(3)]
    assert frames == ["·", "✢", "✳"]
    assert moves.status_glyph("pending") == "●"
    assert moves.status_glyph("stopped") == "■"


def test_every_chip_symbol_is_its_own():
    symbols = [c["symbol"] for c in moves.CHIP.values()]
    unique = set(symbols)
    dupes = [s for s in unique if symbols.count(s) > 1]
    assert dupes == ["↩"]  # the one shared symbol: the safe way back out
    assert symbols.count("↩") == 3  # Keep, Cancel, Back
