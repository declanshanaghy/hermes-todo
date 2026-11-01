# Test cases

**Coverage: 40 of 40 cases (100%)** are covered by `pytest` (see Developing in
the README for the command).

Each row is one behaviour worth testing. "Covered by" names the test (by its
title) that would fail if the behaviour broke. The rows port claude-todo's
phase-1 scope (its `docs/test-cases.md` M, S, D rows and the guard) to this
plugin; the pane, drafts, exec and tracing rows arrive with their phases.

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

## Not yet ported (arrive with their phases)

Claude-todo rows for the pane (C, K, U, V, B), drafts (R), exec and prompts
(E), linking and tracing (L), control and turn ends (P, T), the sync loop
(Y) and migration (G) belong to phases 2–5 of the port; the plan is
`tmp/plans/migrate-hermes.md` in the claude-todo repo. Each phase adds its
rows here with the tests that cover them.
