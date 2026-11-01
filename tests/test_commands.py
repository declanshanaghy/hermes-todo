"""The /todo dispatcher: every chip is a verb of its own name, working
exactly where its chip is drawn, refusing elsewhere in one line
(claude-todo C24-C26)."""

import pytest

from hermes_todo import commands, exec as exec_mod

REPO = "/repos/a"


class FakeApi(exec_mod.Api):
    def __init__(self):
        self.launches = []
        self.cancels = []

    def launch(self, goal, working_directory, correlation):
        self.launches.append({"goal": goal, "cid": correlation})
        return {"subagent_id": f"child-{len(self.launches)}"}

    def cancel(self, handle, reason):
        self.cancels.append(handle)
        return True

    def inject(self, text):
        return False


@pytest.fixture
def disp(store):
    api = FakeApi()
    runner = exec_mod.Runner(lambda: store, api)
    d = commands.Dispatcher(store_of=lambda: store, runner_of=lambda: runner,
                            dir_of=lambda: REPO)
    d.api = api
    return d


def _run(d, raw):
    return d(raw, session_id="me")


def test_add_and_a_bare_title_adds(disp, store):
    assert _run(disp, "add fix the login loop").startswith("● #1 added")
    assert _run(disp, "frobnicate the widget").startswith("● #2 added")  # no verb: a title
    assert store.get(REPO, 2)["title"] == "frobnicate the widget"


def test_list_answers_counts_and_rows(disp, store):
    _run(disp, "add fix login")
    out = _run(disp, "list")
    assert "2 Todos" not in out
    assert "1 Todos ●1" in out
    assert "● #1 fix login" in out


def test_every_chip_verb_works_where_its_chip_is_drawn(disp, store):
    _run(disp, "add fix login")
    assert "started" in _run(disp, "exec 1")
    assert "paused" in _run(disp, "pause 1")
    assert "resumed" in _run(disp, "resume 1")
    assert "stopped" in _run(disp, "stop 1")
    assert "refining" in _run(disp, "refine 1")
    assert "stopped" in _run(disp, "stop 1")  # back to a state that offers restart
    assert "restarted" in _run(disp, "restart 1")
    assert "✕ #1 removed" not in _run(disp, "delete 1")  # it has run: delete refused


def test_a_refused_verb_names_the_status_and_the_verbs_that_apply(disp, store):
    _run(disp, "add fix login")
    out = _run(disp, "pause 1")
    assert "pending" in out and "pause doesn't apply" in out


def test_exec_and_auto_reach_a_stopped_todo_too(disp, store):
    _run(disp, "add fix login")
    _run(disp, "exec 1")
    disp.api.cancels.clear()
    store.update(REPO, 1, {"status": "paused"})
    store.update(REPO, 1, {"status": "stopped"})
    assert "started" in _run(disp, "exec 1")


def test_a_prompting_todo_refuses_exec_and_fire(disp, store):
    _run(disp, "add fix login")
    store.start_drafting(REPO, 1)
    out = _run(disp, "exec 1")
    assert "doesn't apply" in out
    out = _run(disp, "fire")
    assert "still drafting" in out


def test_fire_auto_runs_the_lowest_pending_todo(disp, store):
    _run(disp, "add first")
    _run(disp, "add second")
    out = _run(disp, "fire")
    assert "firing #1" in out
    assert store.get(REPO, 1)["status"] == "running"
    assert store.get(REPO, 2)["status"] == "pending"


def test_fire_with_nothing_pending_says_so(disp, store):
    assert "Nothing to fire" in _run(disp, "fire")


def test_reset_takes_a_drafting_todo_back(disp, store):
    _run(disp, "add fix login")
    store.update(REPO, 1, {"status": "drafting"})
    assert "reset" in _run(disp, "reset 1")
    assert store.get(REPO, 1)["status"] == "pending"
    # once it has run, reset stops it instead — staged through legal moves
    store.update(REPO, 1, {"status": "drafting"})
    store.update(REPO, 1, {"status": "running"})
    store.update(REPO, 1, {"status": "paused"})
    store.update(REPO, 1, {"status": "drafting"})
    _run(disp, "reset 1")
    assert store.get(REPO, 1)["status"] == "stopped"


def test_rename_needs_a_number_and_a_title(disp, store):
    _run(disp, "add fix login")
    assert "new title" in _run(disp, "rename 1")
    assert _run(disp, "rename 1 fix the loop").startswith("✎ #1 renamed")
    assert "already called that" in _run(disp, "rename 1 fix the loop")


def test_takeover_of_another_sessions_todo_stops_it_here(disp, store):
    _run(disp, "add fix login")
    store.update(REPO, 1, {"status": "drafting", "owner": "other"})
    store.update(REPO, 1, {"status": "running", "task_id": "t9"})
    assert "taken over here" in _run(disp, "takeover 1")
    todo = store.get(REPO, 1)
    assert todo["status"] == "stopped"
    assert todo["task_id"] is None
    assert todo["owner"] == "me"


def test_a_target_matches_by_number_hash_or_fragment(disp, store):
    _run(disp, "add fix the login loop")
    _run(disp, "add ship the release")
    assert "started" in _run(disp, "exec #2")
    store.update(REPO, 2, {"status": "stopped"})
    assert "refining" in _run(disp, "refine release")
    assert 'No todo matches "nope"' in _run(disp, "exec nope")


def test_leave_folds_a_just_stopped_todo(disp, store):
    _run(disp, "add fix login")
    _run(disp, "exec 1")
    _run(disp, "stop 1")
    assert "folded away" in _run(disp, "leave 1")


def test_details_answers_prompt_run_and_calls(disp, store):
    _run(disp, "add fix login")
    store.update(REPO, 1, {"prompt": "Fix the loop", "status": "drafting"})
    store.update(REPO, 1, {"status": "running", "task_id": "c1"})
    store.link_agent(REPO, 1, "c1")
    store.record_call("x1", "c1", "terminal", "{}", "ok", "ok", 2)
    out = _run(disp, "details 1")
    assert "prompt: Fix the loop" in out
    assert "1 worker(s), 1 recorded call(s)" in out


def test_help_lists_every_verb(disp):
    out = _run(disp, "help")
    for verb in ("exec", "auto", "pause", "stop", "resume", "refine", "restart", "takeover"):
        assert f"/todo {verb}:" in out


def test_bare_todo_answers_usage(disp):
    assert "Usage:" in _run(disp, "")
