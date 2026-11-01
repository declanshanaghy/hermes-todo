"""hermes-todo — registration.

The whole port on the Hermes plugin surface:

- the todo_* tools (add, backlog, update) over the guarded store
- the drafter: a ctx.llm structured call per add/rename (phase 2)
- the exec runner: a subagent_lifecycle worker per todo in its own
  worktree, goal = the ported protocol prompt (phase 3)
- the /todo command: every chip is a verb of its own name (phase 3)
- call tracing and session reconcile over the observer hooks (phase 4)
- the TUI pane: widgets/ fetches dashboard/plugin_api.py's route (phase 5)

The imports live inside ``register`` so this file also imports cleanly
as a plain module (pytest picks the repo root up as a package because
plugin.yaml needs ``__init__.py`` beside it).
"""

HOOKS = {
    "subagent_start": "on_subagent_start",
    "subagent_stop": "on_subagent_stop",
    "post_tool_call": "on_post_tool_call",
    "on_session_start": "on_session_start",
    "on_session_end": "on_session_end",
    "on_session_finalize": "on_session_end",
    "on_session_reset": "on_session_reset",
}


def register(ctx):
    """Wire schemas to handlers, the command, the hooks and the runner."""
    from . import commands, schemas, tools, trace
    from . import exec as exec_mod

    # The drafter's LLM lane (host-owned credentials, fail-closed gate).
    try:
        tools.LLM = getattr(ctx, "llm", None)
    except Exception:
        tools.LLM = None

    runner = exec_mod.Runner(tools.get_store, exec_mod.HermesApi(ctx))
    dispatcher = commands.Dispatcher(
        store_of=tools.get_store, runner_of=lambda: runner, dir_of=tools.list_dir
    )

    ctx.register_tool(
        name="todo_add", toolset="todo", schema=schemas.TODO_ADD, handler=tools.todo_add
    )
    ctx.register_tool(
        name="todo_backlog", toolset="todo", schema=schemas.TODO_BACKLOG, handler=tools.todo_backlog
    )
    ctx.register_tool(
        name="todo_update",
        toolset="todo",
        schema=schemas.TODO_UPDATE,
        handler=tools.todo_update,
    )

    ctx.register_command(
        "todo",
        handler=dispatcher,
        description="The repo's todo backlog: add, list, exec, auto, fire, stop, resume — /todo help for all",
        args_hint="<verb> [<n|#n|title>]",
    )

    for hook, callback in HOOKS.items():
        ctx.register_hook(hook, getattr(trace, callback))
