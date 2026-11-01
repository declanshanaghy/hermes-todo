# hermes-todo

A native Hermes plugin that keeps a per-repo backlog of todos that Hermes
agents run: each todo works in its own git worktree, lands as one commit
(never force-pushed), and every status move it may make is guarded by one
transitions table. A port of [claude-todo](https://github.com/declanshanaghy/claude-todo)'s
spec to the Hermes plugin surface — the spec is ported, not the code.

## Status

Phase 1 of the port (see `docs/architecture.md` for the phase plan):

- **Done** — plugin package, SQLite store (WAL, revision-guarded writes),
  the nine-state transitions table + guard, `todo_add` / `todo_backlog` /
  `todo_update` tools, pytest suite.
- **Next** — drafting on add via `ctx.llm`; exec via `ctx.subagent_lifecycle`
  with the ported protocol prompt (worktree per todo, squash/rebase/no-force
  landing); call tracing via `pre`/`post_tool_call`; the TUI pane.

## Layout

```
plugin.yaml       manifest (tools the plugin provides)
__init__.py       register(ctx): wires schemas to handlers
schemas.py        tool schemas — what the LLM sees
tools.py          tool handlers — always answer JSON, never raise
db.py             the store: WAL, lists keyed by repo, revision guard
transitions.json  the one table of moves a todo may make
transitions.py    the guard: can_move, moves_from, move_refusal
docs/             architecture.md, test-cases.md (the ported spec)
tests/            pytest suite (one test per test-cases row)
```

## Developing

```bash
# tests (no dependencies beyond pytest)
uv run --no-project --with pytest --python 3.12 python -m pytest tests/ -q

# the same checks catalog CI runs
hermes plugins validate .
hermes plugins doctor . --ci
```

The database lives at `<hermes home>/plugin-data/hermes-todo/data.db`
(one per profile). A done todo leaves the list after its keep period;
`HERMES_TODO_DONE_TTL_MINUTES` overrides it (0 keeps).

## Installing

```bash
hermes plugins install declanshanaghy/hermes-todo
```

or from a checkout: `hermes plugins install /path/to/hermes-todo`.
