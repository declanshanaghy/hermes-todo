# Architecture

hermes-todo is a native Hermes plugin that keeps a per-repo backlog of todos
that Hermes agents run: each todo works in its own git worktree, lands as one
commit, and every status move it may make is guarded by one table. It is a
port of claude-todo's spec (docs/architecture.md there) to the Hermes plugin
surface; the spec is ported, not the code.

## The pieces

| Piece | Role | Source |
|---|---|---|
| Registration | Registers the `todo_*` tools, the `/todo` command and the observer hooks | `__init__.py` |
| Tools | `todo_add`, `todo_backlog`, `todo_update` — what the LLM calls; JSON answers, errors included, never a raise | `tools.py`, `schemas.py` |
| Store | One SQLite database (WAL) per Hermes profile, lists keyed by repo, revision-guarded writes, plus the agents/calls/session-end tables | `db.py` |
| Contract | The transitions table every status write passes, and its guard | `transitions.json`, `transitions.py` |
| Drafter | A `ctx.llm` structured call that drafts candidate prompts on add and rename; the todo is prompting until they're stored | `drafter.py` |
| Moves | The chips a state offers — shared by the `/todo` verbs and the pane, so they can't drift — and each chip's symbol | `moves.py` |
| Runner | Launches each todo's worker (`ctx.subagent_lifecycle`) in its own worktree with the ported protocol prompt; pauses, stops, resumes; falls back to `ctx.inject_message` from an idle session | `exec.py` |
| Protocol | The goal texts: the git rules, the reporting rules, exec/restart/resume prompts, the calls digest | `protocol.py` |
| Command | `/todo <verb> [<n\|title>]` — every chip is a verb of its own name | `commands.py` |
| Tracing | Links a worker by its correlation id, records its calls with input and response, settles a dead worker, reclaims stale todos | `trace.py` |
| Pane | The dashboard route (`/api/plugins/hermes-todo/`) and the TUI widgets that fetch it | `dashboard/plugin_api.py`, `widgets/` |

## Running a todo

Drafting starts when the todo is added, so Exec has its prompts ready:

```mermaid
sequenceDiagram
  actor You
  participant H as plugin (any session)
  participant D as drafter (ctx.llm)
  participant W as worker (subagent, own worktree)
  You->>H: /todo add <title>
  H->>D: structured call: 3 candidate prompts
  H-->>You: ● added — drafting
  D-->>H: drafts stored (todo pending again)
  You->>H: /todo exec <n> (or /todo auto, /todo fire)
  H->>W: launch, goal = protocol prompt, working_directory = its worktree
  W->>H: todo_update: running … done/failed (branch, landed)
  H-->>You: the pane and the card follow along
```

A launch from an idle session (no active agent turn) fails closed in Hermes;
the runner then hands the same protocol prompt to the session's own agent as
a queued user turn (`ctx.inject_message`, consented via
`allow_gateway_injection`). If neither works, the todo goes back where it
was and the error says so. Pause and Stop cancel the worker cooperatively;
Resume launches a fresh worker on the kept worktree, briefed with the run's
recorded calls. A worker that ends without reporting is settled by
`subagent_stop` — a todo is never left running by a dead worker.

## Where state lives

| State | Where | Why there |
|---|---|---|
| Todos | One SQLite database from `plugin_db("hermes-todo")` — `<hermes home>/plugin-data/hermes-todo/data.db`, WAL — keyed by the repo the session is in (each worktree is its own repo; outside a repo, the home directory) | Shared by every session on the machine; survives restarts, updates and removals |
| The list's revision | A `lists` row per repo directory | The revision guard (below) |
| Runtime session state | `ctx.state` (phase 3+: drafts in flight, prompt handoffs) | Belongs to one session |

## Writing without losing edits

Sessions share the database file, so each write says which revision of the
list it was computed from:

```mermaid
sequenceDiagram
  participant H as plugin (any session)
  participant Q as SQLite (WAL)
  H->>Q: BEGIN IMMEDIATE · UPDATE lists SET rev = rev + 1 WHERE dir = D AND rev = R
  alt rev was still R
    Q-->>H: rowcount 1 · write rows · COMMIT
  else another session wrote first
    Q-->>H: rowcount 0 · ROLLBACK
    H->>H: re-read the revision, apply the same edit again (up to 5 tries)
  end
```

A write beaten five times raises `GiveUp`; the tool answers with the error
and never claims the add. The one-script-per-process constraint of
claude-todo disappears (Python holds the connection), but the revision
guard stays: sessions still share the file, and `BEGIN IMMEDIATE` plus the
guarded rev bump makes a lost update impossible.

## A todo's life

A todo is always in one of nine states; `transitions.json` is the one table
of the moves it may make, and `Store.update` is the one guard every status
write passes. A move the table doesn't allow writes nothing — not the other
fields of the same report either — and answers with the moves that are
allowed. Python imports the JSON directly, so there is no mirror to
regenerate; a test drives every `(from, to)` pair through the store and
checks the guard follows the table exactly.

```mermaid
stateDiagram-v2
  direction TB
  [*] --> pending: add
  pending --> prompting: drafter spawned
  prompting --> pending: drafts stored / drafter gone

  pending --> drafting: exec
  drafting --> planning: multi-step
  drafting --> running: simple → one agent
  planning --> running: plan approved

  running --> paused: pause
  paused --> drafting: resume
  running --> stopped: stop
  paused --> stopped: stop
  running --> done: worker reports landed
  running --> failed: worker reports not landed

  stopped --> drafting: refine · restart · resume
  failed --> drafting: refine · restart · resume

  drafting --> pending: reset (never ran)
  planning --> pending: reset (never ran)
  drafting --> stopped: reset (has run)
  planning --> stopped: reset (has run)

  prompting --> [*]: delete
  pending --> [*]: delete (never ran)
  done --> [*]: expires
```

| State | Meaning | Who moves it on |
|---|---|---|
| prompting | Its drafter is writing prompt drafts, so nothing may start it yet | The drafter's drafts or its end |
| pending | Ready to run | You, via exec |
| drafting | The prompt is being written or refined | The worker, via `todo_update` |
| planning | Multi-step: plan being settled | The worker, via `todo_update` |
| running | An agent is working in its worktree | The worker's report once it ends |
| paused | Halted with its run kept, so resume picks up where it was | You |
| stopped | Halted for good; its worktree and branch are kept | You |
| done | Landed (or committed, with no remote) | Expires after the keep period |
| failed | Finished without landing; its worktree and branch are kept | You |

`todo_update` marks a todo run (`has_run`) the first time it turns running,
stamps `done_at` once when it turns done, and stores the prompt, mode, task
and run ids, summary, branch and landed hash as they become known. A done
todo leaves the list after its keep period (`HERMES_TODO_DONE_TTL_MINUTES`,
default 5 minutes; 0 keeps); a failed one stays.

## The tools

| Tool | Does | Refuses with |
|---|---|---|
| `todo_add {title}` | Adds a pending todo, returns its number | Usage, when the title is empty |
| `todo_backlog {} | The repo's backlog as JSON (after expiring done todos) | — |
| `todo_update {n, …}` | Reports prompt/mode/status/summary/branch/landed/task ids through the guard | The refusal naming the allowed moves; `no todo #n`; usage without a number |

Every tool answers a JSON string, errors included, and never raises into the
host.

## Landing

Each todo works on its own branch in its own worktree, commits as it goes,
and lands on the base branch as one squashed commit, rebased onto whatever
other todos landed meanwhile and checked again — never force-pushed; a
conflict that can't be resolved with confidence aborts the rebase, pushes
nothing and fails the todo, keeping its branch. The landing message reads
`<area>: <imperative>` (72 columns at most) followed by the why, the checks,
and `Todo: #N`. The protocol prompt (the launch goal text) carries these
rules verbatim from claude-todo's `hooks/protocol.ts`.

## Divergences from claude-todo (deliberate)

- The plugin launches the worker itself (`ctx.subagent_lifecycle.launch`);
  there is no prompt-submit machinery to route work through the chat, so the
  `Queued`/held-prompt states of claude-todo have no analog. The one
  exception: an exec typed while the session is idle hands the protocol
  prompt to the session's own agent as a queued turn.
- The drafter is a structured `ctx.llm` call, not a spawned agent — cheaper,
  and it can never be mistaken for the todo's run.
- One worker per todo (the "simple" path). Hermes has no Workflow analog;
  multi-step work is the worker's own business, so `planning` stays in the
  table but no code path enters it yet.
- Pause is a cooperative cancel plus a kept run; Hermes has no hard
  task-freeze, so Resume is always a fresh worker briefed from the record
  (what claude-todo does for an unwakeable agent).
- Agent "turns" (text between calls) are not in the observer payloads; the
  call browser shows the recorded calls and the worker's terminal summary.
- Answers are JSON (the Hermes tool convention), not plain text.
- The transitions table is read straight from `transitions.json`; the TS
  mirror (`hooks/transitions.ts`) and its regeneration script don't exist
  here. The TLC model check stays a dev-time option in the claude-todo repo;
  the table is the same one.
- The store is the profile's `plugin_db` database, not one file per repo
  directory; lists are rows keyed by repo path.

## The pane's data path (the one open seam)

The TUI widget is JS in the TUI process; the store lives in the gateway
process. Widgets' only I/O is `fetch`, so the pane reads the plugin's
dashboard route (`dashboard/plugin_api.py`, mounted at
`/api/plugins/hermes-todo/`) — served by `hermes dashboard` and the Desktop
app, not by a plain `hermes --tui` session. The widgets in `widgets/`
therefore take a base URL, a repo dir and a token as constants, and the
route's run verbs answer with a note to type the `/todo` verb in the chat
(runs belong to the session that owns the todo). An upstream
plugin-served widget data route would close the seam; until then the
Desktop pane (whose SDK has first-class backend REST) is the
fully-supported surface. `docs/hermes-orchestration.md` §5 has the details
and the rendered mockups.
