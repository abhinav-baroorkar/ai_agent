# Team setup (a reusable recipe)

How the owner runs this project with several local Claude sessions. Any new
manager session can rebuild the team from this file. Current state of the work
is in `manager-handoff.md`, not here.

## Roles

| Role | Does | Does not |
| --- | --- | --- |
| **Primary** (manager) | Plans, writes briefs, sends branches to review, merges to `main`, pushes, owns the main checkout and `api-contract.md` | Write product code |
| **Staff SDE** | Main coder: server, core pipeline | Touch UI files |
| **Design expert** | Face, UI, anything people see | Change the API without Primary |
| **Jack of all tasks** | Flexible helper: research, side modules, voice stages | |
| **Grunt work** | Small, cheap tasks: config, scaffolding, rewrites of plan/decisions/README | |
| **Code review session** | Reviews a branch diff, reports findings | Edit code |
| **Documentation engineer** | Keeps `manager-handoff.md`, today's devlog, `team.md` and `User_reference/` current from Primary's updates | Edit product code or files owned by another branch |

One role per session. Add or drop roles as the work needs.

## Hub and spoke

- Every session talks **only** to Primary. No session-to-session messages.
- Primary answers questions, assigns tasks, and is the only one who merges and pushes.
- A blocked or unsure worker asks Primary. Nobody guesses on anything that changes
  the API contract.
- Reports go to Primary, in the format below. Primary relays what the owner needs.

## Worktrees

- The main checkout belongs to Primary. Workers never edit, commit or switch
  branches there.
- Each worker gets its own worktree, git-ignored, under `Dependencies/worktrees/`:

  ```
  git fetch origin
  git worktree add Dependencies/worktrees/<branch-name> -b <branch> origin/main
  ```
- Python env per worktree: `cd <worktree>/AI_Agent && uv sync` (makes a local `.venv`).
  Do not source `scripts/env.sh` there: it would share one venv between worktrees.
- A worker touches only the files its task names. Anything else: ask Primary.
- Shared team files live in `Dependencies/team/` (git-ignored, local):
  `team-rules.md` and `api-contract.md`. Primary owns both.

## Flow for one task

1. **Brief.** Primary sends: goal, branch name, files allowed, done criteria.
2. **Build.** The worker creates its worktree and commits on its branch with
   conventional commits. No push, no merge, no PR.
3. **Gate before "done".** From `<worktree>/AI_Agent`: `uv run ruff check .`,
   `uv run ruff format --check .`, `uv run mypy`, `uv run pytest -q`. All pass.
4. **Report** to Primary (format below).
5. **Review.** Primary sends the branch to the code review session. It reports
   findings to Primary. Nothing is posted on GitHub.
6. **Fix.** Primary forwards findings; the worker fixes on the same branch and
   reports again. Repeat until the reviewer approves.
7. **Merge.** Primary merges into `main` in dependency order, rebases and re-locks
   if two branches touched `pyproject.toml` / `uv.lock`, runs all checks and a
   privacy scan, then pushes once the owner has said go.

## Report format

- Line 1: one sentence with the result.
- Then: branch and commit ids; what was built; test numbers; new dependencies and
  their licences; what is not done or not verified; questions.
- A one-line "done" is enough for small updates (for example a doc event folded in).

## Rules every session follows

- Read `CLAUDE.md` and `team-rules.md` first.
- Max 400 lines per file; the persona name never appears in `AI_Agent/src/`; no send
  function for email anywhere; type hints and mypy strict; a fake for every stage so
  tests need no GPU; timeouts on every network call; secrets only in `.env`.
- New dependency: check its licence fits Apache-2.0 and say so in the report.
- Ask Primary before: `sudo`, system packages, deleting files outside your
  worktree, anything that costs money, any network call to a service other than
  GitHub, PyPI, Hugging Face and the local Ollama.
- **Privacy:** the repo is public. No names of people, emails, IPs, tokens, serials
  or addresses in any committed file. Say "the owner" and "household members".
- Never put real data from work systems through a session (owner's standing rule).

## Stay inside the project folder (owner's rule, 2026-11-14, strict)

- Change nothing outside the project folder (the repo root). No other folder, no
  other project. Reading elsewhere is allowed; writing, moving and deleting are not.
- That includes caches and downloads. Before any install or model download, set
  (with `SASHA_ROOT` pointing at the repo root):
  ```
  export HF_HOME=$SASHA_ROOT/Dependencies/hf
  export UV_CACHE_DIR=$SASHA_ROOT/Dependencies/uv-cache
  export XDG_CACHE_HOME=$SASHA_ROOT/Dependencies/cache
  ```
  Still don't set `UV_PROJECT_ENVIRONMENT`: each worktree keeps its own `.venv`.
- Any program the project starts keeps its files inside the project too. A browser
  window uses `--user-data-dir` under `Data/`.
- Don't pull or delete Ollama models; use the ones already installed.
- If a session already wrote something outside the folder, it does not delete it. It
  tells Primary the path.

## Reusing code from an earlier project

The owner allows reusing code from an earlier project of his. Rules for doing it are in the team rules.

## Where documentation lives

| Folder | Reader | Content |
| --- | --- | --- |
| `AI_reference/` (committed) | Team and Primary, to resume after a session loses context | `manager-handoff.md`, `plan.md`, `decisions.md`, `research-notes.md`, `team.md`, `devlog/YYYY-MM-DD.md` (one file per day) |
| `User_reference/` (git-ignored, main checkout only) | The owner. Short plain English | `STATUS.md`, `HOW_TO_RUN.md`, `DECISIONS_FOR_YOU.md` |

## Recreate the team

1. Open one session per role in the repo folder. Name each by its role.
2. Tell each session: read `CLAUDE.md` and `Dependencies/team/team-rules.md`,
   and talk only to Primary.
3. Primary reads `manager-handoff.md`, runs `git worktree list` and `git branch -a`,
   asks each session for a status report, and updates the handoff table.
