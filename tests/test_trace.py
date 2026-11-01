"""The tracing half: linking by correlation id, recording a linked child's
calls with input and response, settling a worker that died without
reporting, and the stale reclaim (claude-todo's L, T and S18 rows)."""

import pytest

from hermes_todo import trace, tools

REPO = "/repos/a"


@pytest.fixture
def wired(store, monkeypatch):
    monkeypatch.setattr(tools, "_STORE", store)
    monkeypatch.setattr(tools, "list_dir", lambda: REPO)
    trace._linked.clear()
    trace._loaded = True  # no db recovery to fake here
    yield store
    trace._linked.clear()
    trace._loaded = False


def _launch(wired, n=1, child="child-1"):
    wired.add(REPO, "Fix login")
    wired.update(REPO, n, {"status": "drafting"})
    wired.update(REPO, n, {"status": "running"})
    trace.on_subagent_start(correlation_id=f"todo:{REPO}:{n}", child_session_id=child)


def test_a_launch_with_our_correlation_links_its_child(wired):
    _launch(wired)
    agent = wired.agent_of("child-1")
    assert agent and agent["dir"] == REPO and agent["n"] == 1


def test_a_foreign_correlation_links_nothing(wired):
    trace.on_subagent_start(correlation_id="review-42", child_session_id="c9")
    assert wired.agent_of("c9") is None


def test_a_linked_childs_calls_are_recorded_with_input_and_response(wired):
    _launch(wired)
    trace.on_post_tool_call(
        session_id="child-1", tool_name="terminal",
        args={"command": "pytest -q"}, result="40 passed",
        tool_call_id="tc-1", duration_ms=120, status="ok",
    )
    calls = wired.calls_for("child-1")
    assert len(calls) == 1
    assert calls[0]["tool"] == "terminal"
    assert "pytest -q" in calls[0]["args"]
    assert "40 passed" in calls[0]["result"]
    assert calls[0]["state"] == "ok"
    assert calls[0]["ms"] == 120


def test_a_call_from_an_unlinked_session_writes_nothing(wired):
    wired.add(REPO, "Fix login")
    trace.on_post_tool_call(session_id="somebody-else", tool_name="terminal",
                             args={}, result="", tool_call_id="tc-2", status="ok")
    assert wired.conn.execute("SELECT COUNT(*) AS c FROM calls").fetchone()["c"] == 0


def test_a_failed_call_records_its_error_state(wired):
    _launch(wired)
    trace.on_post_tool_call(session_id="child-1", tool_name="terminal",
                            args={"command": "make"}, result="exit 2",
                            tool_call_id="tc-3", status="error", duration_ms=5)
    assert wired.calls_for("child-1")[-1]["state"] == "err"


def test_a_worker_that_landed_and_reported_is_left_settled(wired):
    _launch(wired)
    wired.update(REPO, 1, {"status": "done", "summary": "landed", "landed": "abc"})
    trace.on_subagent_stop(child_session_id="child-1", child_status="SUCCEEDED",
                           child_summary="done")
    assert wired.get(REPO, 1)["status"] == "done"
    assert wired.agent_of("child-1")["status"] == "done"


def test_a_worker_that_died_without_reporting_fails_its_todo(wired):
    _launch(wired)
    trace.on_subagent_stop(child_session_id="child-1", child_status="FAILED",
                           child_summary="checks never passed")
    todo = wired.get(REPO, 1)
    assert todo["status"] == "failed"  # never left running
    assert "checks never passed" in todo["summary"]
    assert wired.agent_of("child-1")["status"] == "failed"


def test_a_cancelled_worker_stops_its_todo(wired):
    _launch(wired)
    trace.on_subagent_stop(child_session_id="child-1", child_status="CANCELLED",
                           child_summary="")
    assert wired.get(REPO, 1)["status"] == "stopped"


def test_the_link_map_survives_a_process_restart(wired):
    _launch(wired)
    trace._linked.clear()
    trace._loaded = False
    wired.update(REPO, 1, {"status": "running"})
    trace.on_post_tool_call(session_id="child-1", tool_name="terminal",
                             args={}, result="", tool_call_id="tc-9", status="ok")
    assert len(wired.calls_for("child-1")) == 1  # recovered from the agents table


def test_an_ended_session_hands_its_todos_to_whoever_goes_on(wired):
    _launch(wired)
    wired.update(REPO, 1, {"owner": "old-session"})
    trace.on_session_end(session_id="old-session")
    gone = wired.reclaim_stale(REPO, "new-session")
    assert gone == [1]
    todo = wired.get(REPO, 1)
    assert todo["status"] == "stopped"
    assert todo["owner"] == "old-session"  # the row says whose it was


def test_a_session_reset_notes_the_old_session(wired):
    trace.on_session_reset(old_session_id="s-1", session_id="s-2")
    assert "s-1" in wired.ended_sessions()


def test_a_running_todo_of_a_live_session_is_not_reclaimed(wired):
    _launch(wired)
    wired.update(REPO, 1, {"owner": "still-here"})
    trace.on_session_end(session_id="unrelated")
    assert wired.reclaim_stale(REPO, "new-session") == []
