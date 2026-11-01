"""The todo_* tools: add, list and update as the LLM calls them, answering
JSON for errors too, never raising."""

import json
import time

from hermes_todo import tools


def _call(handler, args, **kwargs):
    return json.loads(handler(args, **kwargs))


# ---------- add ----------


def test_add_stores_a_title_only_todo(wired):
    res = _call(tools.todo_add, {"title": "Fix the login redirect loop"}, session_id="s1")
    assert res == {"ok": True, "added": 1, "title": "Fix the login redirect loop", "status": "pending"}
    assert _call(tools.todo_backlog, {})["todos"][0]["status"] == "pending"


def test_add_with_no_title_answers_usage_and_stores_nothing(wired):
    res = _call(tools.todo_add, {"title": "   "}, session_id="s1")
    assert "usage" in res["error"]
    assert _call(tools.todo_backlog, {})["count"] == 0


# ---------- list ----------


def test_list_answers_the_backlog_as_json(wired):
    _call(tools.todo_add, {"title": "one"}, session_id="s1")
    _call(tools.todo_add, {"title": "two"}, session_id="s1")
    res = _call(tools.todo_backlog, {})
    assert res["count"] == 2
    assert [t["n"] for t in res["todos"]] == [1, 2]
    assert res["todos"][0]["title"] == "one"


def test_list_answers_each_repo_its_own(wired, monkeypatch):
    _call(tools.todo_add, {"title": "one"}, session_id="s1")
    monkeypatch.setattr(tools, "list_dir", lambda: "/repos/other")
    assert _call(tools.todo_backlog, {})["count"] == 0


def test_a_done_todo_past_its_ttl_leaves_the_list(wired, monkeypatch):
    monkeypatch.setenv("HERMES_TODO_DONE_TTL_MINUTES", "5")
    _call(tools.todo_add, {"title": "one"}, session_id="s1")
    _call(tools.todo_update, {"n": 1, "status": "drafting"}, session_id="s1")
    _call(tools.todo_update, {"n": 1, "status": "running"}, session_id="s1")
    _call(tools.todo_update, {"n": 1, "status": "done", "landed": "abc"}, session_id="s1")
    wired.conn.execute(
        "UPDATE todos SET done_at = ? WHERE n = 1", (time.time() - 10 * 60,)
    )
    res = _call(tools.todo_backlog, {})
    assert res["count"] == 0


# ---------- update ----------


def test_update_reports_a_change_and_list_reads_it_back(wired):
    _call(tools.todo_add, {"title": "Fix login"}, session_id="worker-1")
    assert _call(tools.todo_update, {"n": 1, "status": "drafting"}, session_id="worker-1")["ok"]
    res = _call(
        tools.todo_update,
        {"n": 1, "status": "running", "prompt": "Fix it", "mode": "agent", "task_id": "t42"},
        session_id="worker-1",
    )
    assert res["ok"]
    assert res["me"] == "worker-1"
    todo = _call(tools.todo_backlog, {})["todos"][0]
    assert todo["status"] == "running"
    assert todo["prompt"] == "Fix it"
    assert todo["task_id"] == "t42"
    assert todo["has_run"] is True


def test_update_of_an_unknown_todo_answers_an_error(wired):
    res = _call(tools.todo_update, {"n": 99, "status": "drafting"}, session_id="s1")
    assert "no todo #99" in res["error"]


def test_update_refuses_a_move_the_table_does_not_allow_naming_its_moves(wired):
    _call(tools.todo_add, {"title": "Fix login"}, session_id="s1")
    res = _call(tools.todo_update, {"n": 1, "status": "done", "summary": "tried to skip ahead"}, session_id="s1")
    assert "can't go from pending to done" in res["error"]
    assert "drafting" in res["error"]
    todo = _call(tools.todo_backlog, {})["todos"][0]
    assert todo["status"] == "pending"
    assert todo["summary"] is None  # nothing of the report was written


def test_update_with_no_number_answers_usage(wired):
    res = _call(tools.todo_update, {"status": "drafting"}, session_id="s1")
    assert "usage" in res["error"]


def test_update_with_an_unknown_field_answers_an_error(wired):
    _call(tools.todo_add, {"title": "Fix login"}, session_id="s1")
    res = _call(tools.todo_update, {"n": 1, "bogus": "x"}, session_id="s1")
    assert "unknown todo fields" in res["error"]


def test_a_tool_never_raises_even_when_the_store_gives_up(store, monkeypatch):
    import hermes_todo.db as db

    real_rev = store.rev
    monkeypatch.setattr(store, "rev", lambda dir: real_rev(dir) - 1)
    monkeypatch.setattr(tools, "_STORE", store)
    monkeypatch.setattr(tools, "list_dir", lambda: "/repos/a")
    res = _call(tools.todo_add, {"title": "one"}, session_id="s1")
    assert "add failed" in res["error"]
    assert "beat this write 5 times" in res["error"]


# ---------- where a list lives ----------


def test_list_dir_is_the_git_repo_the_session_is_in(monkeypatch, tmp_path):
    import subprocess

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    monkeypatch.setattr(tools, "current_cwd", lambda: str(tmp_path))
    assert tools.list_dir() == str(tmp_path)


def test_list_dir_outside_a_repo_is_the_home_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(tools, "current_cwd", lambda: str(tmp_path))
    from pathlib import Path

    assert tools.list_dir() == str(Path.home())
