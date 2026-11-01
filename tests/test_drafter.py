"""The drafter: drafts on add and rename, the prompting lifecycle, the
stale-key guard (claude-todo's R rows)."""

import json

import pytest

from hermes_todo import drafter, tools


class FakeLLM:
    def __init__(self, drafts=None, text=None):
        self.calls = []
        self.drafts = drafts if drafts is not None else [
            {"label": "fix loop", "prompt": "Fix the redirect loop in login.ts"},
            {"label": "rewrite auth", "prompt": "Rewrite the auth flow end to end"},
            {"label": "add test", "prompt": "Add a regression test for the loop"},
        ]
        self.text = text

    def complete_structured(self, messages, schema=None, max_tokens=None, purpose=None):
        self.calls.append({"messages": messages, "purpose": purpose})
        if self.text is not None:
            class R:
                data = None
                text = self.text
            return R()
        class R:
            data = {"drafts": self.drafts}
            text = ""
        return R()


@pytest.fixture
def wired(store, monkeypatch):
    monkeypatch.setattr(tools, "_STORE", store)
    monkeypatch.setattr(tools, "list_dir", lambda: "/repos/a")
    # A synchronous drafter backend: drafts land before the call returns.
    def backend(dir, n, title, key, others):
        drafts = drafter.run(tools.LLM, n, title, others)
        store.set_drafts(dir, n, key, drafts)
    monkeypatch.setattr(drafter, "BACKEND", backend)
    yield store


def test_add_turns_prompting_until_its_drafts_are_stored(wired, monkeypatch):
    monkeypatch.setattr(tools, "LLM", FakeLLM())
    res = json.loads(tools.todo_add({"title": "Fix login"}, session_id="s1"))
    assert res["drafting"] is True
    todo = wired.get("/repos/a", 1)
    assert todo["status"] == "pending"  # drafts stored by the sync backend
    assert todo["drafts"] and todo["drafts"][0]["label"] == "fix loop"
    assert todo["prompt"] is None  # drafts never touch the chosen prompt


def test_the_drafter_is_asked_for_three_concrete_drafts(wired, monkeypatch):
    llm = FakeLLM()
    monkeypatch.setattr(tools, "LLM", llm)
    tools.todo_add({"title": "Fix login"}, session_id="s1")
    assert len(llm.calls) == 1
    assert "exactly 3 drafts" in llm.calls[0]["messages"][0]["content"]
    assert llm.calls[0]["purpose"] == "todo.drafter"


def test_a_drafter_without_an_llm_lane_leaves_the_todo_pending(wired):
    res = json.loads(tools.todo_add({"title": "Fix login"}, session_id="s1"))
    assert res["drafting"] is False
    assert wired.get("/repos/a", 1)["status"] == "pending"


def test_a_drafter_that_answers_nothing_stores_no_drafts(wired, monkeypatch):
    monkeypatch.setattr(tools, "LLM", FakeLLM(text="I couldn't parse that repo"))
    tools.todo_add({"title": "Fix login"}, session_id="s1")
    todo = wired.get("/repos/a", 1)
    assert todo["drafts"] is None or todo["drafts"] == []
    assert todo["status"] == "pending"


def test_parse_drafts_takes_fenced_or_bare_json_only():
    good = json.dumps({"drafts": [{"label": "a", "prompt": "do a"}]})
    assert drafter.parse_drafts(f"```json\n{good}\n```") == [{"label": "a", "prompt": "do a"}]
    assert drafter.parse_drafts(good) == [{"label": "a", "prompt": "do a"}]
    assert drafter.parse_drafts("no json here") == []
    assert drafter.parse_drafts('{"drafts": "not a list"}') == []
    # drafts without a label and a prompt are dropped
    assert drafter.parse_drafts('{"drafts": [{"label": "x"}, {"label": "y", "prompt": "p"}]}') == [
        {"label": "y", "prompt": "p"}
    ]


def test_a_rename_clears_the_drafts_and_drafts_again(wired, monkeypatch):
    monkeypatch.setattr(tools, "LLM", FakeLLM())
    tools.todo_add({"title": "Fix login"}, session_id="s1")
    old_key = wired.conn.execute("SELECT draft_key FROM todos WHERE n = 1").fetchone()["draft_key"]
    res = json.loads(tools.todo_update({"n": 1, "title": "Fix the login loop"}, session_id="s1"))
    assert res.get("renamed") == "drafting prompts again"
    todo = wired.get("/repos/a", 1)
    assert todo["title"] == "Fix the login loop"
    new_key = wired.conn.execute("SELECT draft_key FROM todos WHERE n = 1").fetchone()["draft_key"]
    assert new_key != old_key


def test_drafts_under_an_old_launch_key_are_refused_as_stale(wired, monkeypatch):
    monkeypatch.setattr(tools, "LLM", FakeLLM())
    tools.todo_add({"title": "Fix login"}, session_id="s1")
    stale = wired.conn.execute("SELECT draft_key FROM todos WHERE n = 1").fetchone()["draft_key"]
    # a rename relaunched the drafter under a new key
    tools.todo_update({"n": 1, "title": "Fix the login loop"}, session_id="s1")
    res = wired.set_drafts("/repos/a", 1, stale, [{"label": "late", "prompt": "too late"}])
    assert res.get("stale") is True
    todo = wired.get("/repos/a", 1)
    assert all(d["label"] != "late" for d in (todo["drafts"] or []))


def test_late_drafts_arriving_once_it_started_change_nothing(wired, monkeypatch):
    monkeypatch.setattr(tools, "LLM", FakeLLM())
    tools.todo_add({"title": "Fix login"}, session_id="s1")
    key = wired.conn.execute("SELECT draft_key FROM todos WHERE n = 1").fetchone()["draft_key"]
    wired.update("/repos/a", 1, {"status": "drafting"})
    wired.update("/repos/a", 1, {"status": "running", "prompt": "chosen"})
    res = wired.set_drafts("/repos/a", 1, key, [{"label": "late", "prompt": "arrived late"}])
    assert res["ok"] is True  # stored…
    todo = wired.get("/repos/a", 1)
    assert todo["status"] == "running"  # …but the run is untouched
    assert todo["prompt"] == "chosen"


def test_the_drafts_prompt_carries_the_other_todos(wired, monkeypatch):
    llm = FakeLLM()
    monkeypatch.setattr(tools, "LLM", llm)
    tools.todo_add({"title": "first"}, session_id="s1")
    tools.todo_add({"title": "second"}, session_id="s1")
    asked = llm.calls[-1]["messages"][0]["content"]
    assert "first" in asked  # the other todo names the context
