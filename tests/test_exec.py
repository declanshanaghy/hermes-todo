"""The exec runner: launch, the worktree, the inject fallback, and the
halts — over a fake lifecycle API (claude-todo's E rows, the Hermes way)."""

import subprocess

import pytest

from hermes_todo import exec as exec_mod
from hermes_todo import protocol

REPO = "/repos/a"


class FakeApi(exec_mod.Api):
    def __init__(self, fail_launch=False, inject_ok=False):
        self.launches = []
        self.cancels = []
        self.injected = []
        self.fail_launch = fail_launch
        self.inject_ok = inject_ok

    def launch(self, goal, working_directory, correlation):
        if self.fail_launch:
            raise RuntimeError("No active Hermes parent session is available.")
        self.launches.append({"goal": goal, "wt": working_directory, "cid": correlation})
        return {"subagent_id": f"child-{len(self.launches)}", "state": "PENDING"}

    def cancel(self, handle, reason):
        self.cancels.append((handle, reason))
        return True

    def inject(self, text):
        if not self.inject_ok:
            return False
        self.injected.append(text)
        return True


@pytest.fixture
def runner(store):
    return exec_mod.Runner(lambda: store, FakeApi())


def _repo(tmp_path):
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "--allow-empty", "-q", "-m", "init"],
        check=True,
    )
    return str(tmp_path)


def test_exec_launches_a_worker_in_its_own_worktree(store, runner, tmp_path):
    repo = _repo(tmp_path)
    store.add(repo, "Fix login")
    res = runner.exec(repo, 1, "me")
    assert res["ok"] and res["launched"]
    launch = runner.api.launches[0]
    assert launch["cid"] == exec_mod.correlation_id(repo, 1)
    assert launch["wt"] and launch["wt"] != repo  # its own worktree, not the checkout
    assert "Todo: #1" in launch["goal"]  # the landing message rules ride along
    assert "todo_update" in launch["goal"]  # and the reporting rules
    todo = store.get(repo, 1)
    assert todo["status"] == "running"
    assert todo["has_run"] is True
    assert todo["task_id"] == "child-1"
    assert todo["branch"]


def test_auto_takes_the_first_draft_in_the_goal(store, runner, tmp_path):
    repo = _repo(tmp_path)
    store.add(repo, "Fix login")
    store.set_drafts(repo, 1, store.start_drafting(repo, 1),
                     [{"label": "best", "prompt": "Fix the redirect loop"}])
    runner.exec(repo, 1, "me", auto=True)
    goal = runner.api.launches[0]["goal"]
    assert "go on with it as it is" in goal
    assert "Fix the redirect loop" in goal


def test_a_launch_from_an_idle_session_falls_back_to_injection(store, tmp_path):
    repo = _repo(tmp_path)
    store.add(repo, "Fix login")
    api = FakeApi(fail_launch=True, inject_ok=True)
    runner = exec_mod.Runner(lambda: store, api)
    res = runner.exec(repo, 1, "me")
    assert res.get("injected") is True
    assert api.injected and "Todo: #1" in api.injected[0]
    assert store.get(repo, 1)["status"] == "drafting"  # the chat settles it


def test_a_dropped_exec_puts_the_todo_back(store, tmp_path):
    repo = _repo(tmp_path)
    store.add(repo, "Fix login")
    api = FakeApi(fail_launch=True, inject_ok=False)
    runner = exec_mod.Runner(lambda: store, api)
    res = runner.exec(repo, 1, "me")
    assert "error" in res
    assert store.get(repo, 1)["status"] == "pending"  # back where it was


def test_stop_and_pause_cancel_the_worker_and_keep_the_todo(store, runner, tmp_path):
    repo = _repo(tmp_path)
    store.add(repo, "Fix login")
    runner.exec(repo, 1, "me")
    assert runner.pause(repo, 1, "me")["ok"]
    assert store.get(repo, 1)["status"] == "paused"
    assert runner.stop(repo, 1, "me")["ok"]
    todo = store.get(repo, 1)
    assert todo["status"] == "stopped"
    assert todo["task_id"]  # kept, for the resume briefing
    assert len(runner.api.cancels) == 2


def test_resume_launches_a_fresh_worker_briefed_with_the_calls(store, tmp_path):
    repo = _repo(tmp_path)
    store.add(repo, "Fix login")
    store.update(repo, 1, {"status": "drafting"})
    store.update(repo, 1, {"status": "running", "task_id": "child-1"})
    store.link_agent(repo, 1, "child-1")
    store.record_call("c1", "child-1", "terminal", '{"command": "ls"}', "ok", "ok", 3)
    store.update(repo, 1, {"status": "paused"})
    api = FakeApi()
    runner = exec_mod.Runner(lambda: store, api)
    res = runner.resume(repo, 1, "me")
    assert res["resumed"] is True
    assert "terminal" in api.launches[0]["goal"]  # the digest rides along
    assert store.get(repo, 1)["status"] == "running"


def test_resume_with_nothing_kept_starts_it_as_exec_would(store, tmp_path):
    repo = _repo(tmp_path)
    store.add(repo, "Fix login")
    store.update(repo, 1, {"status": "drafting"})
    store.update(repo, 1, {"status": "running"})
    store.update(repo, 1, {"status": "paused"})
    store.update(repo, 1, {"status": "stopped", "task_id": None, "handle": None})
    api = FakeApi()
    runner = exec_mod.Runner(lambda: store, api)
    res = runner.resume(repo, 1, "me")
    assert res["launched"] is True  # a fresh worker, not a resume briefing
    assert "resuming todo #1" not in api.launches[0]["goal"]


def test_the_correlation_round_trips():
    cid = exec_mod.correlation_id("/repos/a", 3)
    assert exec_mod.parse_correlation(cid) == ("/repos/a", 3)
    assert exec_mod.parse_correlation("delegate:other") is None
    assert exec_mod.parse_correlation(None) is None


def test_the_goal_carries_the_landing_rules_verbatim(tmp_path):
    repo = _repo(tmp_path)
    goal = protocol.exec_prompt(4, "Fix login", "main", None, auto=False)
    for rule in (
        "Never force-push",
        "git rebase origin/main",
        "Todo: #4",
        "one commit",
        "todo_update",
        "72 characters",
    ):
        assert rule in goal, rule
