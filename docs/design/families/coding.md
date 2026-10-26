# Coding

`coding-repo` measures repository work by AI agents: reading a small unfamiliar project, following its conventions, changing several files, running its tests, and stopping when the request is met. Each task's hidden tests decide whether a solution passes, and collection preserves its diff for inspection. The family uses the votes scorer but stays unpublished (`public = false`). Online preparation supports rendered case families.

## Systems and Samples

Every system that [`family.toml`](../../../families/coding-repo/family.toml) lists runs under `terminus-2`, Harbor's terminal agent, as [Systems](../systems.md#harnesses) describes. Its fingerprint binds the family's harness and the locked Harbor version. Each system uses its resolved completion allowance and high effort or its supported equivalent, with native context management and no family turn cutoff. A task's 1,800-second agent timeout remains an operational watchdog. Each task and system pair runs once, and `gemini-3.8-flash-high` is the reference that anchors the board at 1,000 when a round ranks it.

## Categories

| Category   | The instruction asks for                                                                 | Tasks |
| ---------- | ---------------------------------------------------------------------------------------- | ----: |
| `bug-fix`  | Fixing the bugs behind a user report or a failing suite, sometimes with regression tests |     2 |
| `feature`  | Adding durable behavior across application and persistence boundaries                    |     2 |
| `refactor` | Preserving public behavior while changing publication or persisted data                  |     2 |

## Task Sourcing

Every task is a synthetic Python 3.14 application using the standard library and `unittest`, so no task carries a lockfile. Its instruction and candidate-visible API documentation define every behavior the hidden tests require. The starter implements a usable baseline with a causal state defect or missing integration. The reference provides a complete application and focused regression tests.

| Task                           | Behavior                                                                                      |
| ------------------------------ | --------------------------------------------------------------------------------------------- |
| `resume-ledger-import`         | Import exact CSV amounts with durable identity, atomic batches and resumable checkpoints      |
| `repair-singleflight-cache`    | Isolate tenants and preserve loader generations through cancellation, expiry and invalidation |
| `stabilize-cursor-pagination`  | Traverse an append-only snapshot with stable binary ordering and filter-bound cursors         |
| `deliver-transactional-outbox` | Commit orders and notifications together, then retry delivery with stable payloads and keys   |
| `publish-asset-graph`          | Rewrite and hash an asset graph before atomically publishing a complete release               |
| `migrate-customer-store`       | Migrate persisted names atomically while preserving API responses, reports and relationships  |

A task's `[metadata] author` preserves the public label of the system whose model wrote it, and `author_vendor` records its vendor independently of current participants. Held-out tasks, which keep the board from being fit to the public set, belong under the data directory's `cases/coding-repo/<task>/`, as [Data](../../conventions/data.md#data-classes) describes.

## Task Conventions

Each task follows [Authoring](../../guide/authoring.md#tasks) and adds:

- **Image:** `python:3.14-slim` pinned by its multi-arch index digest, with `git` and `tmux`, a fixed time zone, locale, and commit dates, and the repository from `environment/app/` committed at `/app` as one `chore: import project` commit.

- **Sandbox:** no network for the agent or the verifier, two CPUs, and 2,048 MB of memory per container. The verifier runs in its own container, which receives only `/app`, so the agent cannot reach the interpreter or `/tests`, and a tests-only fix regrades every recorded trial without a model call.

- **Verifier:** `tests/test.sh` runs `tests/verify.py` and writes reward 1 only when every check passes, or 0 when verification fails. `tests/verify_lib.py` copies `/app` to a scratch directory, adds the hidden test modules from `tests/hidden/`, and runs `python3 -m unittest` with a 60-second timeout inside the separate 600-second verifier limit.

  It also fails a workspace whose top-level module or regular package shadows a standard-library one, since `python3 -m unittest` would otherwise import the workspace's own `unittest`.

- **Shared files:** author `tests/verify_lib.py`, `tests/test.sh`, and `environment/Dockerfile` under `families/coding-repo/shared/`, then run `uv run python -m assistant_bench.task_assets` to copy them into each task. Each affected task needs a version bump after it has run.

- **Tests:** hidden tests assert documented behavior through the application's real callers, persisted state and controlled concurrency. Root integration tests run the real verifier against reference solutions, starters and deliberately wrong source variants under `tests/broken/`. Candidates add focused regression tests without test-count, helper-name or patch-shape requirements.

- **Style:** starters, references, hidden tests, verifier scripts and wrong variants share the root formatting, spelling and strict Python 3.14 typing policy. Each task has an import environment resolving its completed reference package, and starters retain their local package imports.

The solution's own code runs inside the verifier's unittest process, beside the hidden tests, so it can still patch the runner or the checks it shares that process with.

A passing reward still needs review because candidate modules execute alongside the hidden tests.

## Scoring

The family's only gate is `tests`, which a solution fails when its reward is below 1. Collection records that verdict alongside the solution diff. A tests-only task revision regrades the recorded solution without another model call.

## Collected Diffs

Collection computes each solution's diff of `/app` against the task's `environment/app/`, without the repository's history or the caches that Python's tools write.

A diff shows only what a solution changed, so a convention in the repository's AGENTS.md is visible only where the diff touches it.
