# Test cases

**Coverage: 93 of 93 cases (100%)** are covered by `pytest` (see Developing in
the README for the command).

Each row is one behaviour worth testing. "Covered by" names the test (by its
title) that would fail if the behaviour broke. The rows port claude-todo's
`docs/test-cases.md` to this plugin, phase by phase.

## The transitions table and its guard

| ID | Behaviour | Covered by |
|---|---|---|
| G1 | Every table move is between known ends (`[*]` or one of the nine statuses) | every table move is between known ends |
| G2 | `can_move` allows exactly the table's moves for every `(from, to)` pair, self-loops aside (a write that keeps the status is never a move) | can_move allows exactly the table for every pair |
| G3 | `moves_from` lists each target once, in the table's order | moves_from lists each target once in table order |
| G4 | A refusal names the todo and the moves that are allowed | a refusal names the todo and the moves allowed |
| G5 | The life the architecture doc draws is walkable: add → draft → run → done, the pause/stop loop, refine/restart/resume | the life the docs draw is walkable |
| G6 | Moves the doc forbids are refused: never straight to running or done, done leaves only by expiry, stopped and failed never go back to pending | moves the docs forbid are refused |
| G7 | Before the todo exists, the table allows only `[*] → pending` | moves_from before the todo exists |

## The store

| ID | Behaviour | Covered by |
|---|---|---|
| S1 | Todos number from 1 in their list, and a title is stored trimmed | add numbers todos in order and strips the title |
| S2 | Each repo has its own list and its own numbering | each repo has its own list |
| S3 | A repo with no todos starts empty | a repo with no todos starts empty |
| S4 | An empty title adds nothing | add with no title raises |
| S5 | `update` sets the fields it is given, and marks the todo run the first time it turns running | update sets fields and marks run once running |
| S6 | `update` stamps `done_at` once and keeps the branch and landed hash (claude-todo E1b) | update stamps done once and keeps branch and landed |
| S7 | `update` of an unknown todo answers an error (claude-todo M3) | update of an unknown todo answers an error |
| S8 | A refused move writes none of the report, not the other fields either (claude-todo M5) | a refused move writes none of the report |
| S9 | For every `(from, to)` pair the store takes the move exactly when the table allows it, and keeps the old status otherwise (claude-todo M6/M7) | a move the table allows is taken from every state |
| S10 | An unknown field is refused before anything is written | update with unknown fields is refused before writing |
| S11 | Delete removes a todo that never ran (prompting or pending) | delete removes a todo that never ran |
| S12 | Delete refuses a todo that has run, keeping its record | delete refuses a todo that ran |
| S13 | A done todo expires after its keep period; a failed one stays (claude-todo D1) | a done todo expires but a failed one stays |
| S14 | A keep period of 0 keeps done todos (claude-todo D2) | a ttl of zero keeps done todos |
| S15 | Each write bumps the list's revision | each write bumps the list revision |
| S16 | A write another session beat is retried on the new revision (claude-todo S5) | a write another session beat is retried on the new revision |
| S17 | A write other sessions keep beating gives up after 5 tries, writing nothing (claude-todo S6) | a write other sessions keep beating gives up |
| S18 | Two stores on one file (two sessions) never lose each other's writes | two stores on one file never lose each other's writes |
| S19 | The database is WAL | the database is wal |
| S20 | `HERMES_TODO_DONE_TTL_MINUTES` overrides the keep period, and 0 keeps; a non-number falls back to the default (claude-todo D2) | done ttl from the environment |

## The tools

| ID | Behaviour | Covered by |
|---|---|---|
| T1 | `todo_add` stores a title-only todo, pending, and `todo_backlog` lists it (claude-todo C1) | add stores a title-only todo |
| T2 | `todo_add` with no title answers with usage and stores nothing (claude-todo C3) | add with no title answers usage and stores nothing |
| T3 | `todo_backlog` answers the backlog as JSON (claude-todo M4) | list answers the backlog as json |
| T4 | `todo_backlog` answers each repo only its own todos (claude-todo S4) | list answers each repo its own |
| T5 | A done todo past its keep period leaves the list (claude-todo D1) | a done todo past its ttl leaves the list |
| T6 | `todo_update` reports a change with the session's id, and `todo_backlog` reads it back (claude-todo M1) | update reports a change and list reads it back |
| T7 | `todo_update` of an unknown todo answers as an error (claude-todo M3) | update of an unknown todo answers an error |
| T8 | `todo_update` refuses a move the table doesn't allow, naming the allowed moves, and writes none of the report (claude-todo M5) | update refuses a move the table does not allow, naming its moves |
| T9 | `todo_update` with no number answers with its usage | update with no number answers usage |
| T10 | `todo_update` with an unknown field answers with what failed (claude-todo S16) | update with an unknown field answers an error |
| T11 | A tool never raises, even when the store gives up: the error names what failed (claude-todo S16) | a tool never raises even when the store gives up |
| T12 | A list lives under the git repo the session is in (claude-todo S1) | list dir is the git repo the session is in |
| T13 | Outside a repo, a list lives under the home directory (claude-todo S2) | list dir outside a repo is the home directory |

## The drafter (phase 2)

| ID | Behaviour | Covered by |
|---|---|---|
| R1 | An add with an LLM lane turns prompting and stores up to 3 drafts without touching the todo's prompt, landing it pending (claude-todo R2, R7) | add turns prompting until its drafts are stored |
| R2 | The drafter is asked for exactly three concrete drafts, as a structured call on the `todo.drafter` purpose (claude-todo R1) | the drafter is asked for three concrete drafts |
| R3 | Without an LLM lane the todo stays pending and Exec drafts in the chat (claude-todo R9) | a drafter without an llm lane leaves the todo pending |
| R4 | An answer with no JSON stores no drafts (claude-todo R13) | a drafter that answers nothing stores no drafts |
| R5 | Drafts parse from fenced or bare JSON; entries without a label and a prompt are dropped | parse drafts takes fenced or bare json only |
| R6 | A rename clears the drafts and drafts again under a fresh key (claude-todo R5) | a rename clears the drafts and drafts again |
| R7 | Drafts under an old launch key are refused as stale (claude-todo R5, R13) | drafts under an old launch key are refused as stale |
| R8 | Drafts arriving once the todo started are stored but change nothing (claude-todo R6) | late drafts arriving once it started change nothing |
| R9 | The drafter's prompt carries the directory's other todos (claude-todo R1) | the drafts prompt carries the other todos |

## The chips and verbs (phase 3)

| ID | Behaviour | Covered by |
|---|---|---|
| V1 | Each state offers exactly the chips claude-todo's docs list for it, in the row's order (claude-todo V8) | each state offers exactly its chips |
| V2 | A todo in flight in another session offers only Takeover and Details (claude-todo P10) | a todo in flight elsewhere offers only takeover |
| V3 | A todo paused before it ever ran belongs to no session (claude-todo S17) | a todo paused before it ever ran belongs to no session |
| V4 | A refusal names the status and the verbs that apply (claude-todo C26) | a refusal names the status and the verbs that apply |
| V5 | A refusal of a done todo says no action applies (claude-todo C26) | a refusal of a done todo says no action applies |
| V6 | The running mark spins through claude-todo's frames | the running mark spins |
| V7 | No two actions share a chip symbol, apart from the safe way back out (claude-todo U16a) | every chip symbol is its own |

## The exec runner (phase 3)

| ID | Behaviour | Covered by |
|---|---|---|
| E1 | Exec launches a worker in its own worktree (never the main checkout), its goal carrying the git rules and the todo's correlation id | exec launches a worker in its own worktree |
| E2 | Auto takes the first draft into the goal, asking nothing (claude-todo E1c) | auto takes the first draft in the goal |
| E3 | A launch from an idle session falls back to injecting the protocol as a queued turn (claude-todo E10, the Hermes way) | a launch from an idle session falls back to injection |
| E4 | A dropped exec (neither launch nor injection worked) puts the todo back where it was (claude-todo E2) | a dropped exec puts the todo back |
| E5 | Pause and Stop cancel the worker cooperatively and keep the todo, its task id kept for the resume briefing (claude-todo P1, P5) | stop and pause cancel the worker and keep the todo |
| E6 | Resume launches a fresh worker on the kept worktree, briefed with the run's recorded calls (claude-todo P20) | resume launches a fresh worker briefed with the calls |
| E7 | Resume with nothing kept starts the todo as Exec would (claude-todo C19) | resume with nothing kept starts it as exec would |
| E8 | The correlation id round-trips through the launch → link path | the correlation round trips |
| E9 | The goal carries the landing rules verbatim: squash, rebase, never force-push, one commit, `Todo: #N` (claude-todo E1a) | the goal carries the landing rules verbatim |

## The /todo command (phase 3)

| ID | Behaviour | Covered by |
|---|---|---|
| C1 | `/todo add` and a bare non-verb first word both add the todo (claude-todo C1, C4) | add and a bare title adds |
| C2 | `/todo list` answers the counts and one row per todo (claude-todo C11) | list answers counts and rows |
| C3 | Every chip verb leads where its chip does, in every state that draws it (claude-todo C24) | every chip verb works where its chip is drawn |
| C4 | A refused verb names the status and the verbs that apply (claude-todo C25, C26) | a refused verb names the status and the verbs that apply |
| C5 | Exec and Auto reach a stopped or failed todo too (claude-todo C24) | exec and auto reach a stopped todo too |
| C6 | A prompting todo refuses exec and fire, saying its prompts are still being drafted (claude-todo R8, E1f) | a prompting todo refuses exec and fire |
| C7 | Fire auto-runs the lowest-numbered pending todo (claude-todo E1d) | fire auto runs the lowest pending todo |
| C8 | Fire with nothing pending says so (claude-todo E1g) | fire with nothing pending says so |
| C9 | Reset takes a drafting todo back to pending, or to stopped once it has run (claude-todo E5) | reset takes a drafting todo back |
| C10 | Rename needs a number and a title; the same title says so (claude-todo C27, E6) | rename needs a number and a title |
| C11 | Takeover of another session's todo stops it here, clearing its run ids (claude-todo P11) | takeover of another session's todo stops it here |
| C12 | A target matches by number, `#n`, or a case-insensitive title fragment; no match says so (claude-todo C8) | a target matches by number hash or fragment |
| C13 | Leave folds a just-stopped todo (claude-todo P6) | leave folds a just stopped todo |
| C14 | Details answers the prompt, summary, run ids and recorded calls (claude-todo E1b, U4) | details answers prompt run and calls |
| C15 | Help lists every verb (claude-todo C30) | help lists every verb |
| C16 | A bare `/todo` answers with its usage (claude-todo C3) | bare todo answers usage |

## Tracing and reconcile (phase 4)

| ID | Behaviour | Covered by |
|---|---|---|
| L1 | A launch with our correlation id links its child to the todo (claude-todo L1) | a launch with our correlation links its child |
| L2 | A foreign correlation links nothing (claude-todo L5) | a foreign correlation links nothing |
| L3 | A linked child's calls are recorded with their input and response, duration and state (claude-todo L7) | a linked child's calls are recorded with input and response |
| L4 | A call from an unlinked session writes nothing (claude-todo L8) | a call from an unlinked session writes nothing |
| L5 | A failed call records its error state (claude-todo L8) | a failed call records its error state |
| L6 | A worker that reported its landing is left settled (claude-todo T1) | a worker that landed and reported is left settled |
| L7 | A worker that died without reporting settles its todo — never left running (claude-todo T2) | a worker that died without reporting fails its todo |
| L8 | A cancelled worker stops its todo (claude-todo T3) | a cancelled worker stops its todo |
| L9 | The link map recovers from the agents table after a process restart (claude-todo S8) | the link map survives a process restart |
| L10 | An ended session hands its in-flight todos to whoever goes on, each stopped with its run kept (claude-todo S18) | an ended session hands its todos to whoever goes on |
| L11 | A session reset notes the old session id (claude-todo S14) | a session reset notes the old session |
| L12 | A running todo of a live session is not reclaimed | a running todo of a live session is not reclaimed |

## Not yet ported

The pane's own rows (claude-todo's C pane frame, K counts, U focus, V view,
B call-browser rows) arrive with the widget's tests; the widget runs in the
TUI process, outside pytest's reach, and is verified by the TUI contract
(`/widgets-reload`, `/todo`) plus the dashboard route's smoke test.
