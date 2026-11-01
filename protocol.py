"""The protocol texts a launched worker runs under, ported from claude-todo's
hooks/protocol.ts. The runner fills <base> with the repo's real base branch
before launch, and the goal text carries the git rules verbatim: each todo
works on its own branch in its own worktree, commits as it goes, and lands
on the base branch as one squashed commit, rebased onto whatever other
todos landed meanwhile and checked again — never force-pushed.
"""

from __future__ import annotations

from typing import Any

TAG_RULES = """Linking rules (the todo pane tracks work by these, so follow them exactly):
- Report every status change with the todo_update tool: {{n: <n>, status: "…"}}.
- You are todo #{n}'s worker; the pane shows your calls under it.
"""


def git_rules(n: int, base: str) -> str:
    return f"""Git rules (the base branch is {base}):
- Work only in your own git worktree, on its own branch; never edit, commit in or check out anything in the main checkout. A worktree starts at the base branch's last commit, so uncommitted changes in the main checkout aren't in it.
- Commit as you go on your branch: small work-in-progress commits are fine there, since they're squashed before landing. Never finish with work uncommitted.
- Validate: run the project's own checks (what its README, package scripts or CI workflow run: tests, lint, typecheck, build). Fix what fails and commit the fixes. If they can't be made to pass, stop: land nothing, and report the failing check.
- Land, only once the checks pass. Other todos will have landed since you started, so:
  1. Squash: git fetch origin, then git reset --soft $(git merge-base HEAD origin/{base}) and git commit with the landing message below. Your branch is now one commit.
  2. git rebase origin/{base}. A conflict usually means another todo running in parallel changed the same files. Read its side (git log -p origin/{base} -- <file>) and resolve keeping both changes' intent; never drop theirs to keep yours. Continue the rebase.
  3. Run every check again on the rebased commit. Fix anything by amending that one commit (git commit --amend), so what lands passes on its own.
  4. git push origin HEAD:{base}. Rejected because the base moved (another todo landed first)? git fetch origin and go back to 2.
  5. A conflict you can't resolve with confidence, or checks that fail after the rebase: git rebase --abort, push nothing, and report the conflicting files and your branch, which keeps the work.
  Never force-push, and push nothing but HEAD:{base}. With no origin remote, commit only and say so.
- The landing message: a summary line of at most 72 characters, "<area>: <what it does, imperative>"; a blank line; why it was needed and what changed, wrapped at 72 columns; the checks it passed; then the line "Todo: #{n}". One todo lands as one commit: no merge, fixup or work-in-progress commits on {base}.
- Report back with the todo_update tool: {{n: {n}, status: "done", summary: "<one line>", branch: "<your branch>", landed: "<the pushed commit's hash>"}} — "done" only if the work landed (or there's no remote and it's committed); otherwise {{n: {n}, status: "failed", summary: "<why not, and where the work is>"}}. Never leave the todo "running".
"""


def exec_prompt(n: int, title: str, base: str, drafts: list[dict[str, str]] | None,
                 auto: bool, refine: bool = False) -> str:
    """The goal a worker runs under for exec, refine or restart. Auto takes
    the first draft and asks nothing; with none, the worker drafts its own
    best prompt from the title and the repo and goes on."""
    if refine:
        first = (
            "1. Refine the current prompt below yourself: keep what it gets right, "
            "make it concrete and self-contained, then run it.\n"
            f"   Current prompt: {drafts}\n"
        )
    elif auto and drafts:
        first = (
            "1. Auto mode: don't ask anyone anything. This prompt was already "
            "drafted for the todo (the best of its drafts); go on with it as it is:\n"
            f"   {drafts[0]['prompt']}\n"
        )
    elif auto:
        first = (
            "1. Auto mode: don't ask anyone anything. Draft the single best prompt "
            "for this todo from its title and this repo, then go on with it.\n"
        )
    elif drafts:
        offered = "\n".join(f"   - {d['label']}: {d['prompt']}" for d in drafts)
        first = (
            "1. These prompts were already drafted for this todo; run the first one:\n"
            f"{offered}\n"
        )
    else:
        first = (
            f'1. Draft the single best prompt for this todo ("{title}") from the '
            "repo (README, the files the title points at), then run it.\n"
        )
    return f"""You are the worker for todo #{n} "{title}". Follow this protocol exactly.
{first}2. Call todo_update {{n: {n}, prompt: "<the final prompt>"}}.
3. Call todo_update {{n: {n}, status: "running"}}, then do the work under the git rules below.
4. On finishing, report as the git rules say.

{git_rules(n, base)}
{TAG_RULES.format(n=n)}"""


def restart_prompt(n: int, title: str, prompt: str, base: str) -> str:
    """Restart from scratch with the current prompt; old worktrees stay."""
    return exec_prompt(n, title, base, [{"label": "current", "prompt": prompt}], auto=True)


def resume_prompt(n: int, title: str, base: str, digest: str) -> str:
    """A fresh worker on the kept worktree, briefed with what the run
    already did (newest last), so it picks up where the old one stopped."""
    return f"""You are the worker resuming todo #{n} "{title}". An earlier worker
was stopped mid-run; you take over the work it kept.
What its run did before it stopped, newest last:
{digest or '   (nothing recorded)'}

1. Your worktree is the one you start in (the kept one): read git log
   origin/{base}..HEAD, git status and git diff to see what's done, and
   finish the task from there.
2. Call todo_update {{n: {n}, status: "running"}} once you're going.
3. Land it under the git rules below, and report as they say.

{git_rules(n, base)}
{TAG_RULES.format(n=n)}"""


def calls_digest(calls: list[dict[str, Any]], limit: int = 24) -> str:
    """A resume's briefing: the run's calls as one line each, newest last."""
    lines = [
        f"   {c['state']} {c['tool']} {(c.get('args') or '')[:90]}".rstrip()
        for c in calls[-limit:]
    ]
    return "\n".join(lines)
