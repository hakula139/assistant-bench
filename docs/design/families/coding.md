# Coding

`coding-repo` measures repository work by AI agents: reading a small unfamiliar project, following its conventions, changing several files, running its tests, and stopping when the request is met. Each task's hidden tests decide whether a solution passes, and collection preserves its diff for inspection. The family uses the votes scorer with `public_voting = true`, so `bench arena prepare` publishes its committed synthetic tasks' passing diffs for voting in production, where the `tests` gate decides each verdict. Its board stays off the site (`public = false`) until its first round closes.

## Systems and Samples

Systems in [`family.toml`](../../../families/coding-repo/family.toml) default to `terminus-2`, Harbor's terminal agent. [`systems.toml`](../../../systems.toml) pins installed agents' CLI releases, as [Systems](../systems.md#harnesses) describes. Each fingerprint binds its harness and version. Each system uses its resolved completion allowance and high effort or its supported equivalent, with native context management and no family turn cutoff. A task's 1,800-second agent timeout remains an operational watchdog. Each task and system pair runs once, and `gemini-3.8-flash-high` is the reference that anchors the board at 1,000 when a round ranks it.

## Categories

| Category   | The instruction asks for                                                                 | Tasks |
| ---------- | ---------------------------------------------------------------------------------------- | ----: |
| `bug-fix`  | Fixing the bugs behind a user report or a failing suite, sometimes with regression tests |     4 |
| `feature`  | Adding behavior across application, protocol and persistence boundaries                  |     3 |
| `refactor` | Preserving public behavior while changing publication, persisted data or execution       |     3 |

The site labels these categories Bug fix, Feature, and Refactor from its glossary.

## Task Sourcing

Every task is a synthetic Python 3.14 application using the standard library and `unittest`, so no task carries a lockfile. Its instruction, its candidate-visible documentation and any standard the instruction names define every behavior the hidden tests require. Harder tasks report symptoms or state a contract, and leave its consequences for the candidate to work out. The starter is a usable baseline whose defect lies in how it keeps state or in a missing integration. The reference provides a complete application and focused regression tests.

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

A task's `[metadata] author` names the model that wrote it, and `author_vendor` records its vendor independently of current participants. Held-out tasks, which keep the board from being fit to the public set, belong under the private repository's `cases/coding-repo/<task>/`, as [Data](../../conventions/data.md#data-classes) describes.

## Task Conventions

Each task follows [Authoring](../../guide/authoring.md#tasks) and adds:

- **Image:** `python:3.14.10-slim-trixie` with `git` and `tmux`. `environment/app/` is committed at `/app` as one `chore: import project` commit, and the image fixes the time zone, the locale and the commit dates, so every build yields the same commit.

- **Sandbox:** no network for the agent or the verifier, two CPUs, and 2,048 MB of memory per container. The verifier runs in its own container, which receives only the agent's `/app`, so nothing the agent ran can reach the verifier's interpreter or `/tests`.

- **Verifier:** reward 1 requires at least one passing workspace test with none failing, then every hidden test passing with none skipped. The workspace tests run from a scratch copy of `/app`. Each run has a 300-second limit, which every instruction states along with the container's two CPUs. Keep each reference's runs within about a tenth of it on a loaded host, which leaves a candidate's own tests the same room. The 900-second verifier watchdog holds both runs, and a run past its limit earns reward 0. Before writing its reward, `tests/test.sh` removes planted `reward.json` and `reward.txt` files, since Harbor's writable log mount lets tested code create them and Harbor prefers JSON.

  Hidden assertions import only the standard library and trusted `tests/sandbox.py`, keeping candidate code outside their process and the workspace off `sys.path`. Application code runs only in sandbox child processes as `nobody`, which cannot signal, trace, or write root's processes and files. Scenarios return JSON observations, so assertions see only the data those scenarios report for the inputs they receive.

  As root, which the verifier is in its container, `verify.py` makes `/tests` unreadable to `nobody`, so a scenario cannot read the trusted values it is checked against, and kills every process of that user after each run and test and before exiting, so the tested code cannot write the verifier or outlive verification and touch the reward after `test.sh` writes it. Since the agent works as root, every instruction tells the candidate that verification runs the code unprivileged from a copy of `/app` it may not write.

  A scenario runs in the application's process, so the boundary cannot stop a solution written against the public scenarios, whether it memorizes their observations or disables their instrumentation. The held-out split under the private repository's `cases/coding-repo/` addresses that contamination.

- **Shared files:** author `environment/Dockerfile`, `solution/solve.sh`, `tests/test.sh`, `tests/verify.py`, `tests/runner.py`, `tests/sandbox.py`, and `tests/host.py` under `families/coding-repo/shared/`, then run `uv run python -m assistant_bench.task_assets` to copy every file there that git does not ignore into each task. It also removes each task's copy of a file that `HEAD` holds under `shared/` and the working tree no longer does. Each affected task needs a version bump after it has run.

- **Reference:** `solve.sh` copies `solution/app/` over `/app`. It holds the files the reference changes or adds, plus the unchanged modules of each application package it changes, since basedpyright resolves a regular package from one search path. A starter edit to one of those modules needs the same edit in its copy.

- **Tests:** keep inputs, expected values and assertions under `tests/hidden/`, and application interactions under `tests/scenarios/`. Use `sandbox.observe` for JSON observations and `sandbox.start` or `sandbox.spawn` for longer-lived or interactive processes. Scenarios may be coroutines. The trusted side reads only scenario JSON and, for HTTP tasks, responses through `http.client`. It opens no application-written file, so persisted state must be observed through the application's API or a stdlib read inside the sandbox. Repository tests run the real verifier against starters, references, broken-reference patches and bypass attempts, require only references to pass, and check that planted reward files are replaced. They also check workspace-result judgment and trusted import boundaries. Candidates add focused regression tests.

- **Style:** starters, references, hidden tests, scenarios and verifier scripts follow the root [formatting, spelling and typing policy](../../conventions/development.md#dev-shell). Each task adds two `[tool.basedpyright] executionEnvironments` entries: one rooted at its `environment/app` with empty `extraPaths`, so the starter resolves only its own modules, then one rooted at the task whose `extraPaths` names its `solution/app`, so a scenario resolves the reference package, and its `tests`, so a hidden test resolves `sandbox`.

## Scoring

The family's only gate is `tests`, which a solution fails when its reward is below 1. Collection records that verdict alongside the solution diff. A tests-only task revision regrades the recorded solution without another model call.

## Collected Diffs

Collection computes each solution's diff of `/app` against the task's `environment/app/`, without the repository's history or the caches that Python's tools write.

A diff shows only what a solution changed, so a convention in the repository's AGENTS.md is visible only where the diff touches it.
