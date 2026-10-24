# Coding

`coding-repo` measures repository work by AI agents: reading a small unfamiliar project, following its conventions, changing several files, running its tests, and stopping when the request is met. Each task's hidden tests decide whether a solution passes, and blind votes compare the solutions that do, as a reviewer reads a pull request. It stays off the site (`public = false`) until it has about 30 tasks and a closed round.

## Systems and Samples

Every system that [`family.toml`](../../../families/coding-repo/family.toml) lists runs under `terminus-2`, Harbor's terminal agent, as [Systems](../systems.md#harnesses) describes. Its fingerprint binds the family's harness and the locked Harbor version. Each system uses its resolved completion allowance and strongest supported effort, with native context management and no family turn cutoff. A task's 1,800-second agent timeout remains an operational watchdog. Each task and system pair runs once, and `gemini-3.8-flash-high` is the reference that anchors the board at 1,000 when a round ranks it.

## Categories

| Category   | The instruction asks for                                                                 | Tasks |
| ---------- | ---------------------------------------------------------------------------------------- | ----: |
| `bug-fix`  | Fixing the bugs behind a user report or a failing suite, sometimes with regression tests |     2 |
| `feature`  | A function, CLI option, or module that the request or the repository's docs specify      |     3 |
| `refactor` | Removing duplication or renaming an API without changing behavior                        |     2 |

## Task Sourcing

Every task is a small synthetic Python project written for the bench, using the standard library and `unittest` alone, so no task carries a lockfile. Each task's instruction reads like a developer's request in chat: it says where the repository is and names every interface the hidden tests call and every rule a structural check enforces, unless the repository's own AGENTS.md or docstrings state it. A reviewer who reads only the instruction and the repository can write tests that agree with the hidden ones, and any check that fails this test is a hidden requirement to remove.

A task's `[metadata] author` names the `systems.toml` system whose model wrote it. Held-out tasks, which keep the board from being fit to the public set, belong under the data directory's `cases/coding-repo/<task>/`, as [Data](../../conventions/data.md#data-classes) describes.

## Task Conventions

Each task follows [Authoring](../../guide/authoring.md#tasks) and adds:

- **Image:** `python:3.13-slim` pinned by its multi-arch index digest, with `git` and `tmux`, a fixed time zone, locale, and commit dates, and the repository from `environment/app/` committed at `/app` as one `chore: import project` commit.

- **Sandbox:** no network for the agent or the verifier, two CPUs, and 2,048 MB of memory per container. The verifier runs in its own container, which receives only `/app`, so the agent cannot reach the interpreter or `/tests`, and a tests-only fix regrades every recorded trial without a model call.

- **Verifier:** `tests/test.sh` runs `tests/verify.py` and always writes a reward, 1 only when every check passes. `tests/verify_lib.py` copies `/app` to a scratch directory, adds the hidden test modules from `tests/hidden/`, and runs `python3 -m unittest`. Each verifier starts at most five subprocesses with a 60-second timeout each, so their timeouts sum to less than the 600-second limit.

  It also fails a workspace whose top-level module or regular package shadows a standard-library one, since `python3 -m unittest` would otherwise import the workspace's own `unittest`, and it runs git under a configuration of its own, so the workspace's `.git/config` and hooks run nothing.

- **Shared files:** author `tests/verify_lib.py`, `tests/test.sh`, and `environment/Dockerfile` under `families/coding-repo/shared/`, then run `uv run python -m assistant_bench.task_assets` to update their standalone task copies. CI checks those copies with `--check`. Each affected task needs a version bump after it has run.

- **Tests the agent writes:** each variant under `tests/broken/<variant>/` holds a `sitecustomize.py` that breaks one part of the agent's own code as Python starts, such as restoring a reported bug, leaving a function unimplemented, or ignoring a command-line option, and the verifier requires the agent's tests to fail under each.

  Subprocesses inherit the variant, so a test that runs the CLI as a child process sees the defect too. The check proves that the tests fail once that part breaks, whatever the agent named its helpers, while a check on test counts or names would accept a test that never exercises it.

- **Style:** `environment/app/` and `tests/hidden/` hold the project under test in its own style, with its intended defects, so the linters skip them as [Development](../../conventions/development.md#pre-commit-hooks) lists. The verifier scripts, the variants, and `solution/solve.sh` follow the bench's own conventions, with Ruff targeting the image's Python 3.13.

  Strict typing checks every authored verifier, probe and fault script on Python 3.13. The candidate repository under `environment/app/` contains the faults the task asks the agent to fix. Hidden tests under `tests/hidden/` exercise the completed solution's APIs, which can be absent from that starter repository. These two input directories stay outside the verifier type-checking scope, preserving the unsolved benchmark. The canonical helper and all generated helper copies remain checked.

The solution's own code runs inside the verifier's unittest process, beside the hidden tests, so it can still patch the runner or the checks it shares that process with.

`fix-pagination` hashes every file in its `tests/` and rejects added ones, which prevents a patch through a test module. A task still cannot rule out a patch in the solution's own modules. A tampered pass settles a failing solution's pairs without a vote, so voters never see it.

## Scoring

The family's only gate is `tests`, which a solution fails when its reward is below 1. Tests settle a pair when they separate the systems, and two failures tie. Different passing solutions are eligible for blind voting, as [Arena](../arena.md#sessions-and-pairs) describes. The board's failed-tests column is each system's share of graded solutions that failed, and its score blends the settled results with recorded votes.

## Voting

The vote page shows the instruction and the two solutions as diffs of `/app` against the task's `environment/app/`, without the repository's history or the caches that Python's tools write, as [Voting](../../guide/voting.md#diffs) describes.

Collection recomputes the diffs, so once votes exist, changing the paths `diffs.LEFT_OUT` leaves out changes the text under recorded votes, and two solutions that differed only in a newly left-out path start to tie, which drops their votes.

The issue tags are `scope`, `conventions`, `complexity`, `testing`, and `clarity`, whose labels and hints the vote page shows. A diff shows only what a solution changed, so a convention in the repository's AGENTS.md is visible only where the diff touches it.
