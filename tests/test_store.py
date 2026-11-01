"""The store: numbering, dir keying, the revision guard, and the one guard
every status write passes."""

import sqlite3
import time

import pytest

from hermes_todo import db, transitions


def _insert_todo(store, dir, n, status, **fields):
    now = time.time()
    store.conn.execute(
        "INSERT INTO todos (dir, n, title, status, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (dir, n, fields.get("title", f"todo {n}"), status, now, now),
    )
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields)
        store.conn.execute(
            f"UPDATE todos SET {sets} WHERE dir = ? AND n = ?",
            [*fields.values(), dir, n],
        )


# ---------- numbering and keying ----------


def test_add_numbers_todos_in_order_and_strips_the_title(store):
    assert store.add("/repos/a", "  Fix login  ") == 1
    assert store.add("/repos/a", "Ship release") == 2
    todo = store.get("/repos/a", 1)
    assert todo["title"] == "Fix login"
    assert todo["status"] == "pending"


def test_each_repo_has_its_own_list(store):
    assert store.add("/repos/a", "one") == 1
    assert store.add("/repos/b", "two") == 1  # its own list, its own numbers
    assert [t["n"] for t in store.todos("/repos/a")] == [1]
    assert [t["n"] for t in store.todos("/repos/b")] == [1]


def test_a_repo_with_no_todos_starts_empty(store):
    assert store.add("/repos/a", "one") == 1
    assert store.todos("/repos/b") == []


def test_add_with_no_title_raises(store):
    with pytest.raises(ValueError):
        store.add("/repos/a", "   ")


# ---------- update through the guard ----------


def test_update_sets_fields_and_marks_run_once_running(store):
    n = store.add("/repos/a", "Fix login")
    assert store.update("/repos/a", n, {"status": "drafting"})["ok"]
    res = store.update(
        "/repos/a",
        n,
        {"status": "running", "prompt": "Fix the redirect loop", "mode": "agent"},
    )
    assert res["ok"]
    todo = store.get("/repos/a", n)
    assert todo["status"] == "running"
    assert todo["prompt"] == "Fix the redirect loop"
    assert todo["mode"] == "agent"
    assert todo["has_run"] is True


def test_update_stamps_done_once_and_keeps_branch_and_landed(store):
    n = store.add("/repos/a", "Fix login")
    store.update("/repos/a", n, {"status": "drafting"})
    store.update("/repos/a", n, {"status": "running"})
    store.update(
        "/repos/a", n, {"status": "done", "branch": "todo-1", "landed": "abc123", "summary": "landed"}
    )
    todo = store.get("/repos/a", n)
    assert todo["status"] == "done"
    assert todo["done_at"] is not None
    assert todo["branch"] == "todo-1"
    assert todo["landed"] == "abc123"


def test_update_of_an_unknown_todo_answers_an_error(store):
    assert "error" in store.update("/repos/a", 99, {"status": "drafting"})


def test_a_refused_move_writes_none_of_the_report(store):
    n = store.add("/repos/a", "Fix login")
    res = store.update("/repos/a", n, {"status": "running", "prompt": "nope"})
    assert "error" in res
    assert "drafting" in res["error"]  # names the moves that are allowed
    todo = store.get("/repos/a", n)
    assert todo["status"] == "pending"
    assert todo["prompt"] is None  # the rest of the write didn't stand either


def test_a_move_the_table_allows_is_taken_from_every_state(store):
    dir = "/repos/a"
    n = 0
    for from_ in transitions.STATUSES:
        for to in transitions.STATUSES:
            n += 1
            _insert_todo(store, dir, n, from_)
            res = store.update(dir, n, {"status": to})
            if transitions.can_move(from_, to):
                assert res.get("ok"), (from_, to, res)
                assert store.get(dir, n)["status"] == to
            else:
                assert "error" in res, (from_, to, res)
                assert store.get(dir, n)["status"] == from_


def test_update_with_unknown_fields_is_refused_before_writing(store):
    n = store.add("/repos/a", "Fix login")
    with pytest.raises(ValueError):
        store.update("/repos/a", n, {"bogus": "x"})


# ---------- delete and expiry ----------


def test_delete_removes_a_todo_that_never_ran(store):
    n = store.add("/repos/a", "Fix login")
    assert store.delete("/repos/a", n)["ok"]
    assert store.get("/repos/a", n) is None


def test_delete_refuses_a_todo_that_ran(store):
    n = store.add("/repos/a", "Fix login")
    store.update("/repos/a", n, {"status": "drafting"})
    store.update("/repos/a", n, {"status": "running"})
    res = store.delete("/repos/a", n)
    assert "error" in res
    assert store.get("/repos/a", n) is not None


def test_a_done_todo_expires_but_a_failed_one_stays(store):
    n = store.add("/repos/a", "Fix login")
    store.update("/repos/a", n, {"status": "drafting"})
    store.update("/repos/a", n, {"status": "running"})
    store.update("/repos/a", n, {"status": "done"})
    store.conn.execute(
        "UPDATE todos SET done_at = ? WHERE dir = ? AND n = ?",
        (time.time() - 10 * 60, "/repos/a", n),
    )
    assert store.expire("/repos/a", 5) == [n]
    assert store.get("/repos/a", n) is None


def test_a_ttl_of_zero_keeps_done_todos(store):
    n = store.add("/repos/a", "Fix login")
    store.update("/repos/a", n, {"status": "drafting"})
    store.update("/repos/a", n, {"status": "running"})
    store.update("/repos/a", n, {"status": "done"})
    store.conn.execute(
        "UPDATE todos SET done_at = ? WHERE dir = ? AND n = ?",
        (time.time() - 10 * 60, "/repos/a", n),
    )
    assert store.expire("/repos/a", 0) == []
    assert store.get("/repos/a", n) is not None


# ---------- the revision guard ----------


def test_each_write_bumps_the_list_revision(store):
    assert store.rev("/repos/a") == 0
    store.add("/repos/a", "one")
    assert store.rev("/repos/a") == 1
    store.add("/repos/a", "two")
    assert store.rev("/repos/a") == 2


def test_a_write_another_session_beat_is_retried_on_the_new_revision(store):
    real_rev = store.rev
    seen = {"stale": True}

    def rev_once_stale(dir):
        r = real_rev(dir)
        if seen["stale"]:
            seen["stale"] = False
            return r - 1  # another session wrote first
        return r

    store.rev = rev_once_stale
    assert store.add("/repos/a", "one") == 1
    assert store.rev("/repos/a") == 1


def test_a_write_other_sessions_keep_beating_gives_up(store, monkeypatch):
    real_rev = store.rev
    monkeypatch.setattr(store, "rev", lambda dir: real_rev(dir) - 1)
    with pytest.raises(db.GiveUp):
        store.add("/repos/a", "one")
    assert store.todos("/repos/a") == []


def test_two_stores_on_one_file_never_lose_each_other_s_writes(tmp_path):
    path = tmp_path / "todos.db"
    c1 = sqlite3.connect(path)
    c2 = sqlite3.connect(path)
    s1, s2 = db.Store(c1), db.Store(c2)
    assert s1.add("/repos/a", "one") == 1
    assert s2.add("/repos/a", "two") == 2  # beat: s1's write landed first
    assert s2.add("/repos/b", "other repo") == 1
    assert s1.rev("/repos/a") == 2
    assert [t["n"] for t in s1.todos("/repos/a")] == [1, 2]
    assert s1.update("/repos/a", 1, {"status": "drafting"})["ok"]
    assert s2.get("/repos/a", 1)["status"] == "drafting"
    c1.close()
    c2.close()


def test_the_database_is_wal(tmp_path):
    path = tmp_path / "todos.db"
    conn = sqlite3.connect(path)
    db.Store(conn)
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.close()
    assert mode == "wal"


def test_the_list_remembers_the_session_that_last_used_it(store):
    assert store.last_session("/repos/a") is None
    store.touch_session("/repos/a", "s1")
    assert store.last_session("/repos/a") == "s1"
    store.touch_session("/repos/a", "s2")
    assert store.last_session("/repos/a") == "s2"  # the latest wins
    store.touch_session("/repos/a", "")  # an empty id never touches
    assert store.last_session("/repos/a") == "s2"
    store.touch_session("/repos/b", "s3")  # a fresh list row, no rev guard needed
    assert store.last_session("/repos/b") == "s3"


def test_done_ttl_from_the_environment(monkeypatch):
    assert db.done_ttl_minutes() == 5.0
    monkeypatch.setenv("HERMES_TODO_DONE_TTL_MINUTES", "30")
    assert db.done_ttl_minutes() == 30.0
    monkeypatch.setenv("HERMES_TODO_DONE_TTL_MINUTES", "0")
    assert db.done_ttl_minutes() == 0.0
    monkeypatch.setenv("HERMES_TODO_DONE_TTL_MINUTES", "not a number")
    assert db.done_ttl_minutes() == 5.0
