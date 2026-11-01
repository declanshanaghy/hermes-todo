"""The drafter: a plain structured LLM call that writes candidate prompts
when a todo is added or renamed, so Exec has its prompts ready before you
press it. Ported from claude-todo's drafting subagent (hooks/protocol.ts
draftsPrompt), but a ctx.llm call instead of an agent — cheaper, and it
cannot be mistaken for the todo's run.

A todo is prompting from the moment its drafter is scheduled until its
drafts are stored; it falls back to pending when the drafter gives none,
fails, or can't run at all (then Exec drafts in the chat instead).
"""

from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any, Callable

logger = logging.getLogger(__name__)

# The JSON schema the structured call is held to.
DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "drafts": {
            "type": "array",
            "maxItems": 3,
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string", "description": "short name, at most 5 words"},
                    "prompt": {"type": "string", "description": "the whole, self-contained task"},
                },
                "required": ["label", "prompt"],
            },
        }
    },
    "required": ["drafts"],
}


def drafts_prompt(n: int, title: str, others: list[str]) -> str:
    """What the drafter is asked, ported from claude-todo's draftsPrompt."""
    listed = "\n".join(f"   - {x}" for x in others) or "   - none"
    return f"""Draft candidate prompts for todo #{n} "{title}" in the todo pane.
The directory's other todos:
{listed}
1. Consider the repo the session is in just enough to make the prompts
   concrete (the README, the files the title points at). Keep it short;
   you are read-only: never edit or write files, never change git state.
2. Write exactly 3 drafts, best first. A label is a short name of at most
   5 words. A prompt is the whole, self-contained task for a worker agent
   that hasn't seen this conversation: what to change, where, and how to
   tell it's done.
3. Answer as only this JSON object, nothing else:
   {{"drafts": [{{"label": "…", "prompt": "…"}}, …]}}"""


def parse_drafts(text: str) -> list[dict[str, str]]:
    """Pull the drafts out of an answer: fenced or bare JSON, and anything
    else answers with none (R13: no JSON stores no drafts)."""
    if not text:
        return []
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return []
    if not isinstance(data, dict):
        return []
    drafts = data.get("drafts")
    if not isinstance(drafts, list):
        return []
    return [
        {"label": d["label"], "prompt": d["prompt"]}
        for d in drafts
        if isinstance(d, dict) and d.get("label") and d.get("prompt")
    ][:3]


def run(llm: Any, n: int, title: str, others: list[str]) -> list[dict[str, str]]:
    """One structured LLM call; a failure answers no drafts (the todo falls
    back to pending, and Exec drafts in the chat)."""
    try:
        result = llm.complete_structured(
            messages=[{"role": "user", "content": drafts_prompt(n, title, others)}],
            schema=DRAFT_SCHEMA,
            max_tokens=1200,
            purpose="todo.drafter",
        )
        data = getattr(result, "data", None)
        if isinstance(data, dict) and isinstance(data.get("drafts"), list):
            return [
                {"label": d["label"], "prompt": d["prompt"]}
                for d in data["drafts"]
                if isinstance(d, dict) and d.get("label") and d.get("prompt")
            ][:3]
        return parse_drafts(getattr(result, "text", "") or "")
    except Exception as exc:  # a failed draft is never fatal
        logger.warning("drafter for #%s failed: %s", n, exc)
        return []


# The scheduling seam: tests inject a synchronous backend; the default
# opens the drafter's own connection and runs on a daemon thread, so the
# tool that scheduled it answers at once.
Backend = Callable[[str, int, str, str, list[str]], None]


def _thread_backend(dir: str, n: int, title: str, key: str, others: list[str]) -> None:
    def work() -> None:
        from . import tools

        store = tools.get_store()
        drafts = run(tools.LLM, n, title, others) if tools.LLM is not None else []
        own = store.reopen()
        try:
            (own or store).set_drafts(dir, n, key, drafts)
        finally:
            if own is not None:
                own.conn.close()

    threading.Thread(target=work, name=f"hermes-todo-drafter-{n}", daemon=True).start()


BACKEND: Backend = _thread_backend


def schedule(dir: str, n: int, title: str, key: str, others: list[str]) -> None:
    """Fire the drafter for a todo's launch key, in the background."""
    BACKEND(dir, n, title, key, others)
