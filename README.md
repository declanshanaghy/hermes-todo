# hermes-todo

A native [Hermes](https://hermes-agent.nousresearch.com) plugin that keeps a
per-repo backlog of todos that Hermes agents run: each todo works as a
worker in its **own git worktree**, lands as **one squashed commit**
(never force-pushed, rebased onto whatever landed meanwhile), and every
status move it may make is guarded by **one transitions table**.

It is a port of [claude-todo](https://github.com/declanshanaghy/claude-todo)
— the Claude Code todo mod this plugin was born from — to the Hermes plugin
surface: its spec, prompts, state machine and pane rules. The spec is
ported, not the code; the migration plan and the phase-by-phase decisions
live in the claude-todo repo (`tmp/plans/migrate-hermes.md` there).

## What it does

- **A backlog per repo** — one SQLite database per Hermes profile, lists
  keyed by the repo the session is in, revision-guarded writes so
  concurrent sessions never lose each other's edits.
- **Drafts on add** — a `ctx.llm` structured call drafts three candidate
  prompts as soon as a todo is added (or renamed); the todo is `prompting`
  until they're stored, and `auto` takes the first.
- **A worker per todo** — `/todo exec` launches a Hermes child session in
  the todo's own worktree, its goal carrying the ported protocol prompt:
  the git rules (commit as you go, squash, rebase `origin/<base>`, re-run
  the checks, never force-push) and the reporting rules. Every call the
  worker makes is recorded with its input and response.
- **Nine guarded states** — `transitions.json` is the one table of moves;
  a move it doesn't allow writes nothing and names the moves that are
  allowed. A dead worker never leaves a todo running; a session that ends
  hands its in-flight todos to whoever goes on.
- **`/todo` verbs and a pane** — every chip is a verb of its own name,
  working exactly where its chip is drawn (`/todo help` for all), and the
  TUI pane (a rail card + a modal) follows the tree live.

## Install

```bash
hermes plugins install declanshanaghy/hermes-todo
```

or from a checkout: `hermes plugins install /path/to/hermes-todo`. No
Python dependencies beyond Hermes itself.

## Use

In any Hermes session inside a git repo:

```
/todo add fix the rev-guard retry        # ● added — drafting its prompts
/todo list                               # the backlog with counts
/todo exec 3                             # run #3, offering its drafts
/todo auto 3                             # run it on its first draft
/todo fire                               # auto-run the lowest pending todo
/todo pause 3 · /todo stop 3 · /todo resume 3
/todo help                               # every verb, one line each
```

The workers report with the `todo_update` tool (the goal text tells them
to), so a run settles itself: `done` with the landed hash, or `failed`
saying why and where the work is.

### Config (plugins.entries.hermes-todo.settings)

| Key | Default | Does |
|---|---|---|
| `allow_gateway_injection` | `false` | Let `/todo exec` hand the protocol prompt to the session's own agent as a queued turn when no in-process worker can be launched (an idle session). Hermes requires this consent for gateway injection. |
| `done_ttl_minutes` | `5.0` | How long a done todo stays in the list; `0` keeps it. `HERMES_TODO_DONE_TTL_MINUTES` overrides both. |

The database lives at `<hermes home>/plugin-data/hermes-todo/data.db`,
one per profile, surviving updates and removals.

### The pane

Two TUI widgets in `widgets/` (copy them to `~/.hermes/tui-widgets/`):
`/todo` opens the modal pane (the tree with its chips, a details view over
the recorded calls), `/todo-card` docks the ambient rail card. They fetch
the plugin's dashboard route (`dashboard/plugin_api.py`, mounted at
`/api/plugins/hermes-todo/`), so a dashboard server or the Desktop app
must be up — a plain `hermes --tui` session serves no route. That seam,
and the rendered mockups, are in `docs/hermes-orchestration.md` §5.

## Layout

```
plugin.yaml       manifest (tools, hooks, config schema)
__init__.py       register(ctx): tools, the /todo command, the hooks, the runner
schemas.py        tool schemas — what the LLM sees
tools.py          tool handlers — always answer JSON, never raise
db.py             the store: WAL, lists keyed by repo, revision guard,
                   agents, calls, session ends, the stale reclaim
transitions.json  the one table of moves a todo may make
transitions.py    the guard: can_move, moves_from, move_refusal
moves.py          the chips per state (the /todo verbs and the pane share it)
drafter.py        the drafts: a ctx.llm structured call per add/rename
protocol.py       the goal texts: git rules, reporting rules, exec/restart/resume
exec.py           the runner: launch in a worktree, cancel, resume; inject fallback
commands.py       the /todo dispatcher
trace.py          linking, call recording, dead-worker settle, stale reclaim
dashboard/        the pane's backend route (manifest.json + plugin_api.py)
widgets/          the TUI pane and rail card (.mjs, fetch the route)
docs/             architecture.md, test-cases.md (93 rows), the orchestration
                   guide, mockups/ (change them with the pane) + assets/
scripts/          render-mockups.sh (headless Chrome, 2× scale)
tests/            pytest suite — one test per test-cases row
```

## Developing

```bash
# tests — 93 of 93 test-cases rows, no dependencies beyond pytest
uv run --no-project --with pytest --python 3.12 python -m pytest tests/ -q

# the same checks catalog CI runs
hermes plugins validate .
hermes plugins doctor . --ci
```

`docs/test-cases.md` lists every behaviour and its covering test; the
"Coverage: 93 of 93" line must match the rows. The mockups in
`docs/mockups/` are the pane's design source — change them with the pane
and re-render with `scripts/render-mockups.sh`.

## Acknowledgements

Ported from [claude-todo](https://github.com/declanshanaghy/claude-todo)
(spec: its `docs/architecture.md`, `hooks/transitions.json`,
`hooks/protocol.ts`, `hooks/moves.ts`, `hooks/marks.tsx`). The pane rules,
the chip palette (statuses 30° apart round the wheel) and the landing
discipline are all its.
