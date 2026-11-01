"""hermes-todo — registration.

Phase 1: the todo_* tools and the guarded store. Later phases add the
drafter (ctx.llm), the exec runner (ctx.subagent_lifecycle with the ported
protocol prompt), call tracing (pre/post_tool_call) and the TUI pane.

The imports live inside ``register`` so this file also imports cleanly as a
plain module (pytest picks the repo root up as a package because plugin.yaml
needs ``__init__.py`` beside it).
"""


def register(ctx):
    """Wire schemas to handlers."""
    from . import schemas, tools

    ctx.register_tool(
        name="todo_add", toolset="todo", schema=schemas.TODO_ADD, handler=tools.todo_add
    )
    ctx.register_tool(
        name="todo_backlog", toolset="todo", schema=schemas.TODO_LIST, handler=tools.todo_backlog
    )
    ctx.register_tool(
        name="todo_update",
        toolset="todo",
        schema=schemas.TODO_UPDATE,
        handler=tools.todo_update,
    )
