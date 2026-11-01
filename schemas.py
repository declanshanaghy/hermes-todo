"""Tool schemas — what the LLM sees."""

TODO_ADD = {
    "name": "todo_add",
    "description": (
        "Add a todo to this repo's backlog. The todo starts pending. "
        "Use when the user asks to queue, save or note a task for later — "
        "it returns the todo's number."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "The todo's title, e.g. 'Fix the login redirect loop'",
            },
        },
        "required": ["title"],
    },
}

TODO_BACKLOG = {
    "name": "todo_backlog",
    "description": (
        "The repo's todo backlog as JSON: each todo's number, title, "
        "status (prompting, pending, drafting, planning, running, paused, "
        "stopped, done, failed), prompt, summary and run details. "
        "Use when the user asks what's queued or where a todo stands."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}

TODO_UPDATE = {
    "name": "todo_update",
    "description": (
        "Report a todo's status and run details as it progresses. Workers "
        "call this for every status change: drafting once work starts, "
        "running once an agent is going, done only if the work landed (or "
        "there is no remote and it's committed), failed saying why and where "
        "the work is, stopped if it was stopped — never leave a todo running. "
        "A status move the todo's state doesn't allow is refused with the "
        "moves that are allowed. Also stores the final prompt, the mode "
        "(agent or workflow), the summary, the branch and the landed commit "
        "hash as they become known."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "n": {"type": "integer", "description": "The todo's number in this repo's list"},
            "prompt": {"type": "string", "description": "The final prompt the todo runs"},
            "mode": {
                "type": "string",
                "enum": ["agent", "workflow"],
                "description": "How the todo runs: one agent, or a workflow",
            },
            "status": {
                "type": "string",
                "enum": [
                    "prompting",
                    "pending",
                    "drafting",
                    "planning",
                    "running",
                    "paused",
                    "stopped",
                    "done",
                    "failed",
                ],
                "description": "The todo's new status (a move its state must allow)",
            },
            "task_id": {"type": "string", "description": "The background task or agent id running it"},
            "run_id": {"type": "string", "description": "The workflow run id, for a workflow"},
            "summary": {"type": "string", "description": "One line: what happened, where the work is"},
            "branch": {"type": "string", "description": "The todo's git branch"},
            "landed": {"type": "string", "description": "The pushed commit's hash, once it landed"},
        },
        "required": ["n"],
    },
}
