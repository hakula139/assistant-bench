# Coding

`coding-repo` measures software work an assistant does inside a repository on a developer's behalf: reading a small unfamiliar project, following its conventions, changing several files, running its tests, and stopping when the request is met. It is a task family scored by votes, so each task's hidden tests decide whether a solution passes, and the owner's blind votes compare the solutions that do, as a reviewer reads a pull request. It stays off the site (`public = false`) until it has about 30 tasks and a closed round.

## Systems and Samples

Every system that [`family.toml`](../../../families/coding-repo/family.toml) lists runs under `terminus-2`, Harbor's terminal agent, as [Systems](../systems.md#harnesses) describes, so a system keeps the name it has on the writing boards while its fingerprint hashes the family's harness. Each system has the same budget: 40 agent turns and 32,000 output tokens per model call, reasoning included. A task's 1,800-second agent timeout is only a safety net, since Harbor reruns a timed-out trial on every later run. Each task and system pair runs once, and `gemini-3.8-flash` is the reference that anchors the board at 1,000 when a round ranks it.

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
- **Sandbox:** no network for the agent or the verifier, two CPUs, and 2,048 MB of memory per container. The verifier runs in its own container, which receives only `/app`, so the agent cannot alter what grades it, and a tests-only fix regrades every recorded trial without a model call.
- **Verifier:** `tests/test.sh` runs `tests/verify.py` and always writes a reward, 1 only when every check passes. `tests/verify_lib.py`, the same in every task, copies `/app` to a scratch directory, adds the hidden test modules from `tests/hidden/`, and runs `python3 -m unittest` with a 60-second timeout per subprocess, so a verifier's timeouts sum to less than its 600-second limit. It also fails a workspace whose top-level module or package shadows a standard-library one, since `python3 -m unittest` would otherwise import the workspace's own `unittest`.
- **Tests the agent writes:** to check them, the verifier runs them against each variant under `tests/broken/<variant>/`, which mirrors `/app` with one part broken, such as the original buggy module or a function left unimplemented, and requires them to fail. A test that never exercises the broken part therefore fails the check, while a check on test counts or names would accept it.
- **Style:** `environment/`, `tests/hidden/`, and `tests/broken/` hold the project under test, in its own style and with its intended defects, so the linters skip them. The verifier scripts and `solution/solve.sh` follow the bench's own conventions, with Ruff targeting the image's Python 3.13.

## Scoring

The family's only gate is `tests`, which a solution fails when its reward is below 1. Each pair of systems on a task is settled without a vote when the tests separate them, ties when both fail, and goes to the owner when both pass with different diffs, as [Arena](../arena.md#sessions-and-pairs) describes. The board's failed-tests column is each system's share of graded solutions that failed, and its score blends the settled results with the owner's votes, leaning on the tests until most pairs of passing solutions have a vote.

## Voting

The vote page shows the instruction and the two solutions as diffs of `/app` against the task's `environment/app/`, without the repository's history or the caches that Python's tools write, as [Voting](../../guide/voting.md#diffs) describes. The issue tags are `scope`, `conventions`, `complexity`, `testing`, and `clarity`, whose labels and hints the vote page shows. A diff shows only what a solution changed, so a convention in the repository's AGENTS.md is visible only where the diff touches it.
