# Coding

`coding-repo` measures repository work by AI agents: reading a small unfamiliar project, following its conventions, changing several files, running its tests, and stopping when the request is met. Each task's hidden tests decide whether a solution passes, and collection preserves its diff for inspection. The family uses the votes scorer but stays unpublished (`public = false`). Online preparation supports rendered case families.

## Systems and Samples

Every system that [`family.toml`](../../../families/coding-repo/family.toml) lists runs under `terminus-2`, Harbor's terminal agent, as [Systems](../systems.md#harnesses) describes. Its fingerprint binds the family's harness and the locked Harbor version. Each system uses its resolved completion allowance and high effort or its supported equivalent, with native context management and no family turn cutoff. A task's 1,800-second agent timeout remains an operational watchdog. Each task and system pair runs once, and `gemini-3.8-flash-high` is the reference that anchors the board at 1,000 when a round ranks it.

## Categories

| Category   | The instruction asks for                                                                 | Tasks |
| ---------- | ---------------------------------------------------------------------------------------- | ----: |
| `bug-fix`  | Fixing the bugs behind a user report or a failing suite, sometimes with regression tests |     4 |
| `feature`  | Adding behavior across application, protocol and persistence boundaries                  |     3 |
| `refactor` | Preserving public behavior while changing publication, persisted data or execution       |     3 |

The site labels these categories Bug fix, Feature, and Refactor from its glossary.

## Task Sourcing

Every task is a synthetic Python 3.14 application using the standard library and `unittest`, so no task carries a lockfile. Its instruction, its candidate-visible documentation and any standard the instruction names define every behavior the hidden tests require. Harder tasks report symptoms or state a contract, and leave its consequences, such as crash points, lease races and protocol corner cases, for the candidate to work out. The starter implements a usable baseline with a causal state defect or missing integration. The reference provides a complete application and focused regression tests.

| Task                           | Behavior                                                                                      |
| ------------------------------ | --------------------------------------------------------------------------------------------- |
| `resume-ledger-import`         | Import exact CSV amounts with durable identity, atomic batches and resumable checkpoints      |
| `repair-singleflight-cache`    | Isolate tenants and preserve loader generations through cancellation, expiry and invalidation |
| `stabilize-cursor-pagination`  | Traverse an append-only snapshot with stable binary ordering and filter-bound cursors         |
| `deliver-transactional-outbox` | Commit orders and notifications together, then retry delivery with stable payloads and keys   |
| `publish-asset-graph`          | Rewrite and hash an asset graph before atomically publishing a complete release               |
| `migrate-customer-store`       | Migrate persisted names atomically while preserving API responses, reports and relationships  |
| `repair-kv-crash-recovery`     | Recover a segmented write-ahead log and its snapshot after a crash at any write               |
| `add-byte-range-requests`      | Serve RFC 9110 byte ranges consistently with validators, compression and keep-alive           |
| `repair-job-leases`            | Lease SQLite queue jobs atomically across processes, with fencing, expiry and backoff         |
| `make-pipeline-incremental`    | Skip exactly the build steps whose content, declaration and code fingerprints still match     |

A task's `[metadata] author` preserves the public label of the system whose model wrote it, and `author_vendor` records its vendor independently of current participants. Held-out tasks, which keep the board from being fit to the public set, belong under the data directory's `cases/coding-repo/<task>/`, as [Data](../../conventions/data.md#data-classes) describes.

## Task Conventions

Each task follows [Authoring](../../guide/authoring.md#tasks) and adds:

- **Image:** `python:3.14-slim` pinned by its multi-arch index digest, with `git` and `tmux`, a fixed time zone, locale, and commit dates, and the repository from `environment/app/` committed at `/app` as one `chore: import project` commit.

- **Sandbox:** no network for the agent or the verifier, two CPUs, and 2,048 MB of memory per container. The verifier runs in its own container, which receives only `/app`, so the agent cannot reach the interpreter or `/tests`, and a tests-only fix regrades every recorded trial without a model call.

- **Verifier:** `tests/test.sh` runs `tests/verify.py` and writes reward 1 only when every check passes, or 0 when verification fails. Harbor leaves `/logs/verifier` writable by everyone, and Docker Desktop's mounts ignore modes anyway, so before writing the reward `test.sh` deletes any `reward.json`, which Harbor would read first, and any non-regular `reward.txt` the tested code left. `verify.py` fails a workspace whose top-level module or regular package shadows a standard-library one, since discovery puts the workspace first on `sys.path`. It then runs the workspace's own tests in one scratch copy of `/app`. In a second copy it replaces the `tests` package with the modules from `tests/hidden/`, so no workspace test module loads beside the hidden tests. Each run is a child process that runs `tests/runner.py`, which discovers and runs the tests with `unittest`. Each run has a 300-second limit, which every instruction states along with the container's two CPUs. That is about ten times the longest hidden run of any reference on a loaded host, and leaves a candidate's own tests the same room. The task's 900-second verifier limit holds both runs, so a run past its limit still earns reward 0.

  `verify.py` never imports the workspace's code and judges each run by the record `runner.py` writes to an inherited file descriptor once its tests finish. The workspace run passes when at least one test passes and none fails. The hidden run passes when exactly the test methods that `verify.py` parses from `tests/hidden/` pass and no class or module fixture fails, so an early exit, a skip, or a test that never loads fails it. The hidden run also fails when a namespace of the `unittest` machinery differs at its end from its state before the application loaded, which catches a patched runner or assertion. The workspace's own tests may configure `unittest`, so this check skips their run.

  As root, which the verifier is in its container, `verify.py` runs both as the `nobody` user with the hidden copy read-only, and kills every process of that user after each run and before exiting, so the tested code cannot write the verifier or the hidden tests, or outlive verification and touch the reward after `test.sh` writes it. Since the agent works as root, every instruction tells the candidate that verification runs the code unprivileged from a copy of `/app` it may not write.

- **Shared files:** author `environment/Dockerfile`, `solution/solve.sh`, `tests/test.sh`, `tests/verify.py`, and `tests/runner.py` under `families/coding-repo/shared/`, then run `uv run python -m assistant_bench.task_assets` to copy every file there into each task. Each affected task needs a version bump after it has run.

- **Reference:** `solve.sh` copies `solution/app/` over `/app`. It holds the files the reference changes or adds, plus the unchanged modules of each application package it changes, since basedpyright resolves a regular package from one search path. A starter edit to one of those modules needs the same edit in its copy.

- **Tests:** hidden tests assert documented behavior through the application's real callers, persisted state and controlled concurrency. Each hidden test method sits directly in the body of a top-level class in a `test*.py` module, since `verify.py` lists the expected tests from those bodies alone. `tests/test_repository.py` runs the real verifier against each starter, its reference, and the broken variants of the reference stored as patches under `tests/fixtures/coding-repo/<task>/`, and only the reference may pass the hidden tests. It also appends each bypass in `tests/fixtures/coding-repo/*.py`, which tries to pass without the behavior, to the reference's application packages in every task, and requires the verifier to reject it and to replace reward files planted beforehand. A table-driven test checks how `verify.py` judges each kind of run record. Candidates add focused regression tests.

- **Style:** starters, references, hidden tests and verifier scripts share the root formatting, spelling and strict Python 3.14 typing policy. Each task adds a `[tool.basedpyright] executionEnvironments` entry whose `extraPaths` names its `solution/app`, so its hidden tests resolve the reference package.

The hidden tests still call the application in their own process, so code written against `runner.py` itself, such as one that forges its record or rewrites a test class in memory, can still pass, and a passing reward needs review. Moving the hidden assertions out of that process, which [#195](https://github.com/hakula139/assistant-bench/issues/195) tracks, must land before the family turns `public` or closes its first `tests` round.

## Scoring

The family's only gate is `tests`, which a solution fails when its reward is below 1. Collection records that verdict alongside the solution diff. A tests-only task revision regrades the recorded solution without another model call.

## Collected Diffs

Collection computes each solution's diff of `/app` against the task's `environment/app/`, without the repository's history or the caches that Python's tools write.

A diff shows only what a solution changed, so a convention in the repository's AGENTS.md is visible only where the diff touches it.
