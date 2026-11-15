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
| **Documentation engineer** | Keeps `manager-handoff.md`, today's devlog, `team.md`, `plan.md`, `decisions.md`, `linkedin-notes.md`, `README.md`, `.env.example` and the owner's notes in `User_reference/` current from Primary's updates. The owner allowed writing in `User_reference/` on 2026-11-14 for notes written for the owner; files the owner put there are still never edited. `CLAUDE.md` wording pending the owner's edit. | Edit product code or files owned by another branch |

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
- Python env per worktree: from the worktree root, `source AI_Agent/scripts/env.sh`
  (bash only, not zsh), then `cd AI_Agent && uv sync`. In a worktree the script keeps the
  worktree's own `.venv` and sends caches to the main checkout's `Dependencies/`. It refuses
  to run outside a bash checkout of the repo, and it clears temp entries older than 7 days. Never set
  `UV_PROJECT_ENVIRONMENT` yourself. A branch made before the script was fixed
  needs `git merge origin/main` first.
- A worktree has no `.env`: no Home Assistant token, and `sasha doctor` skips Home
  Assistant. That is intended; secrets stay with the main checkout. Telemetry from a
  worktree goes to that worktree's `Data/`.
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
   if two branches touched `pyproject.toml` / `uv.lock`, and runs all checks and a
   privacy scan. Primary merges and pushes after the reviewer approves. The owner decided this on 2026-11-14, and `CLAUDE.md` step 3 now says so.

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
- Secrets (owner, 2026-11-14, this project only): the owner allowed sessions to access
  this project's secrets file for now, because it holds nothing sensitive yet. Team
  practice is narrower: code and tests may load the file at run time (`load_dotenv`,
  `reload_secrets`) and may add or check variable names. Sessions don't open, print,
  search, copy or paste the file or any secret value, because anything a session reads
  lands in its transcript; tool output, logs, telemetry, commits and reports never contain
  one. If a permission rule or safety hook blocks a read or a tool, stop and tell Primary;
  don't work around it. Only the owner widens or withdraws this.
- No real-microphone run without the owner present (set 2026-11-14, night). Why: the
  microphone is opened only with the owner at the machine.
- An intermittent test failure is investigated as a possible product bug before anyone calls it
  flaky (2026-11-14: a "flaky" listener test was a real ordering bug in the microphone sign).
  Primary's pre-push check shows the names of failing tests and stops on any failure.
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
- That includes caches and downloads. Before any install or model download, run
  `source AI_Agent/scripts/env.sh` from your worktree (bash only). It points
  `HF_HOME`, `UV_CACHE_DIR` and `XDG_CACHE_HOME` at the main checkout's
  `Dependencies/` and leaves the worktree's own `.venv` alone. It refuses to run outside a
  bash checkout of the repo, and it clears temp entries older than 7 days.
- Any program the project starts keeps its files inside the project too. A browser
  window uses `--user-data-dir` under `Data/`.
- Don't pull or delete Ollama models; use the ones already installed.
- If a session already wrote something outside the folder, it does not delete it. It
  tells Primary the path.

### One allowed write outside the project: the start-at-login link (owner, 2026-11-14)

- The owner allowed a session to create the start-at-login entry for the assistant under
  the home config folder (`~/.config/systemd/user/`).
- Only this: symlinks to the unit files that live inside the project (`systemctl --user
  link <file in the project>`, then `enable`). No other file, no edits to existing files,
  nothing else under the home directory.
- Only the session Primary names does it, only when Primary says the feature is reviewed and
  merged, and it reports the exact paths created and how to undo them (`systemctl --user
  disable --now ...`).
- Everything else in the Stay-inside rule is unchanged.

## Ideas from an earlier project of the owner's: read-only, no copying

The owner first allowed copying on 2026-11-14 and withdrew it the same evening. Now:
- Sessions may read that project only when a brief from Primary says so.
- Copy nothing from it: no files, functions, snippets, comments or config. Understand a
  feature, then write our own code for our own design.
- Public files never name or describe that project. At most: "an idea from an earlier
  project of the owner's".
- The rules are in the team rules file.

## Where documentation lives

| Folder | Reader | Content |
| --- | --- | --- |
| `AI_reference/` (committed) | Team and Primary, to resume after a session loses context | `manager-handoff.md`, `plan.md`, `decisions.md`, `research-notes.md`, `team.md`, `devlog/YYYY-MM-DD.md` (one file per day) |
| `User_reference/` (git-ignored, main checkout only) | The owner. Short plain English | `STATUS.md`, `HOW_TO_RUN.md`, `DECISIONS_FOR_YOU.md`. The owner allowed a documentation session to write notes here on 2026-11-14; files the owner put there are still never edited. `CLAUDE.md` wording pending the owner's edit. |

## Recreate the team

1. Open one session per role in the repo folder. Name each by its role.
2. Tell each session: read `CLAUDE.md` and `Dependencies/team/team-rules.md`,
   and talk only to Primary.
3. Primary reads `manager-handoff.md`, runs `git worktree list` and `git branch -a`,
   asks each session for a status report, and updates the handoff table.
