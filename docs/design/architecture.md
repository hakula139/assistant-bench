# Architecture

assistant-bench limits its code to the parts Harbor lacks: resolving systems, rendering cases into Harbor tasks, the `Generate` and `Terminus` agents, the host environment that runs `Generate` without a container, collecting trials into output records, the voting arena, and the ranking and reports, while Harbor schedules trials, runs containers and verifiers, retries failures, and records every job.

## Module Map

```text
src/assistant_bench/
├── cli.py                                  # The Typer `bench` command
├── ci.py                                   # Coding task selection and checks for Harbor Smoke
├── task_assets.py                          # Regeneration of each coding task's shared files
├── jobs.py                                 # Task rendering, Harbor job configs, runs, and collection
├── revisions.py                            # Systems as committed at another revision, for the fingerprint check
├── report.py                               # The report schema and inputs, one results variant per scorer, and closing a round
├── arena/
│   ├── commands.py                         # Publication, evidence and private administration commands
│   ├── evidence.py                         # Replay of immutable receipt and control history
│   ├── exports.py                          # Bounded capture and verification of registered evidence cuts
│   ├── management.py                       # Authenticated native Service client
│   ├── preparation.py                      # Explicit solution selection and current provenance checks
│   ├── publication.py                      # Approved numerical and presentation projections
│   ├── publishing.py                       # Guarded release activation and sampling refresh
│   ├── runtime.py                          # Named native targets and Service-only configuration
│   ├── snapshots.py                        # Verified evidence cuts as report inputs and sampling points
│   └── sources.py                          # Native task hash and agent-input proof
├── outputs.py                              # Output records and outputs/<family>.jsonl
├── harnesses/
│   ├── __init__.py                         # System harnesses: a Harbor agent and its builders
│   ├── endpoint.py                         # Endpoint variables and native SDK transports
│   ├── generate.py                         # The single-call agent
│   ├── host.py                             # The Harbor environment for single-call jobs, which starts no container
│   ├── labels.py                           # The kwargs that label a trial for collection
│   ├── messages.py                         # Native Messages streaming and conversation content
│   ├── options.py                          # The kwargs each harness builds from its resolved system
│   ├── responses.py                        # Native SDK results and stateless conversation items
│   ├── terminus.py                         # Harbor's terminus-2 agent through the bench's endpoint
│   └── usage.py                            # Validated measured token counters shared by every transport
├── scoring/
│   ├── gates.py                            # Eligibility gates for voted outputs
│   ├── length.py                           # Shared output length measurement
│   ├── ranking.py                          # Bradley-Terry point fit and descriptive order agreement
│   └── weighting.py                        # Family-wide cumulative actor caps
├── families.py                             # family.toml, gate, case, and vote models, and the local case merge
├── catalog.py                              # Systems, fingerprints, and prices from systems.toml and the nixos-config export
├── generation.py                           # Revision-owned generation protocol and locked native agent version
├── messages.py                             # The style of author-facing messages: code spans and validation errors
├── diffs.py                                # Git-style diffs of a task's workspace against its repository
└── paths.py                                # Repo and data directory locations, and atomic file writes
```

## Import Layers

`src/assistant_bench_arena` owns voting contracts, the durable ledger and native runtime adapters. It never imports the offline `assistant_bench` package. Its prepared statements run through the application's single database owner. Only the offline `arena` modules import its shared contracts, for release preparation and evidence analysis. Contract modules remain importable on CPython without loading the ledger services or the native `worker/` subtree. The thin `src/app.py` and `src/render.py` entrypoints export the application and renderer's native SDK classes.

```text
src/assistant_bench_arena/
├── admission.py              # Purpose-bound signatures and network identities
├── api_models.py             # Browser commands and safe acknowledgments
├── investigation.py          # Private receipt queries and expiring network associations
├── investigation_models.py   # Private receipt filters, results and sampling provenance
├── ledger.py                 # Atomic commands and authenticated command recovery
├── ledger_models.py          # Private evidence, control inputs, and export contracts
├── maintenance.py            # Bounded expiry of private and unaccepted state
├── management_domain.py      # Private management command execution with a fixed principal
├── management_models.py      # Management requests, validation and bounded Service results
├── models.py                 # Shared record policy and blinded browser views
├── policy.py                 # Public admission budgets
├── read_models.py            # Private scheduling projections
├── reads.py                  # Bounded inventory, scheduling, and rendered content reads
├── releases.py               # Shared release identities and persisted-fact validation
├── runtime_models.py         # Signed browser claims and deployment profiles
├── scheduler.py              # Balanced and adaptive pair draws
├── schema.py                 # Current profile inspection and atomic initialization marker
├── schema.sql                # Current schema, created with its profile in one native batch
├── serialization.py          # Canonical JSON and content identities
├── snapshots.py              # Numerical policy and approved presentation contracts
├── transport.py              # Prepared statements and exact database rejection mapping
├── voting.py                 # Authenticated pair acquisition and blinded views
└── worker/
    ├── api.py                # Native request, response and ASGI adapters
    ├── authentication.py     # Cookie ownership, admission and network budgets
    ├── bindings.py           # Native D1, limiter, challenge and body adapters
    ├── diagnostics.py        # Content-free failure stages and exception classes
    ├── http.py               # Browser routes and blinded response handling
    ├── owner.py              # The shared application and database owner
    ├── production.py         # Application HTTP and confined Service entrypoints
    ├── rendering.py          # The isolated renderer entrypoint
    └── settings.py           # Native bindings and validated deployment settings
```

Imports point downward through these layers, so a module never imports one above it:

1. `cli`, `ci`, and `task_assets`
2. `arena.commands` and `revisions`
3. `arena.preparation` and `jobs`
4. The remaining offline `arena` modules
5. `report`
6. `outputs`, `harnesses`, and `scoring`
7. `families` and `catalog`
8. `generation`, `messages`, `paths`, and `diffs`
9. `assistant_bench_arena`

`families` and `outputs` are separated from `jobs` so that the arena and the reports read family settings and output records without the Harbor job code. `report` sits below the arena modules because it owns the report inputs and the fixed votes method, which the arena builds from evidence cuts and seals into each release. A module becomes a subpackage only once it holds several members along one axis, as `harnesses/` does for agents and the environment single-call agents run in, and `scoring/` does for the ways outputs are scored.

## Families and Scorers

A family directory holds `family.toml` and either `cases.toml` or `tasks/`, with `load_family` inferring the source from whichever exists. The coding family also holds `shared/`, the authoring source for the files that `task_assets` copies into every task. The source decides how trials run and what grades them:

| Source          | Agent                  | Verifier | Each output holds                                           |
| --------------- | ---------------------- | -------- | ----------------------------------------------------------- |
| `cases.toml`    | `generate`             | Disabled | The reply, which the family's gates check                   |
| `tasks/<task>/` | The family's `harness` | Enabled  | The task's `reward`, and the diff of its `/app` for `votes` |

In `family.toml`, `scorer` discriminates the settings model, which decides how the outputs rank, and each place that depends on the scorer matches on that model and ends in `assert_never`, so mypy reports any scorer that a `match` misses:

| Scorer  | Sources       | Ranks by                                                                                                      |
| ------- | ------------- | ------------------------------------------------------------------------------------------------------------- |
| `votes` | Both          | Blind pairwise votes, after `gate_failures` settle each pair a gate decides: a case's gates or a task's tests |
| `tests` | `tasks/` only | The mean of each system's rewards                                                                             |

## Data Flow

1. `bench run [<family>...]` renders each case into a Harbor task under the data directory, with `instruction.md` and a `task.toml`, or checks each of a task family's own tasks, builds a job config with one agent per system listed in `family.toml` or per system that `--system` names, and runs it through the Harbor CLI with telemetry off.
2. Harbor runs each trial's agent. A `Generate` trial starts no container: it sends one request through the system's configured API, writes the reply to `output.txt` in the trial's agent logs, and records usage and response provenance on the trial's `AgentContext`. `Terminus` drives the task's container until the model completes the task, with native context management and an operational timeout. Harbor then grades the container's `/app` with the task's tests in a fresh container.
3. `bench run` collects the family's trials into `outputs/<family>.jsonl` once Harbor exits, also after a failed run, and `bench collect` re-collects them. Both price tokens from the catalog and take a task's reward. A `votes` family runs its gates over each reply, or for a task family records the diff of each graded `/app` against the task's `environment/app/` and fails the `tests` gate when the reward is below 1.
4. `bench arena prepare` proves the exact selected solution inputs and public content approvals. `publish` seals and activates the release in the canonical D1 owner. The public [arena](arena.md) records real votes there.
5. `bench arena export` captures an immutable D1 evidence cut. `bench close <family> --snapshot <path>` fits its frozen [ranking](ranking.md) and writes the [report](reports.md) to `reports/<family>/<round>.json`. A `tests` family closes directly from its collected trial rewards with `bench close <family>`.

`bench check [<family>...]` validates each named family, or every family when none is named, and for a `tasks/` family it also runs Harbor's oracle and nop on every task, placing its jobs under `checks/` so that collection never reads them.

## Reruns

Each `bench run` job lists the family's earlier jobs as diff sources, so Harbor skips every trial that an earlier job completed with the same task version and agent config. Adding a system therefore generates only its trials.

Each rendered task's version carries a hash of the bench's own task inputs as build metadata on `jobs.TASK_VERSION`, and Harbor reruns a task whose build metadata changed, so an edited case reruns under its ID. `bench check` and `bench run` render the tasks and install them, removing directories for cases the family no longer has. Collection derives the expected version from current case source and render settings without writing tasks, so an old installed task cannot make stale outputs current. The hash covers the instruction, task name and agent timeout.

The diff also compares each trial's environment, which follows the job's harnesses as [Systems](systems.md#harnesses) describes, so changing it reruns every trial of the jobs it applies to. A Harbor upgrade that changes a `task.toml` default aborts the job, as [Harbor](../research/harbor.md#quirks-of-the-pinned-release) describes.

Authored tasks use semantic versions, whose reuse, regrade, and rerun rules are listed in [Authoring](../guide/authoring.md#tasks). Harbor aborts a job whose task changed without a bump, so `bench check` and `bench run` first name each task whose content differs from what an earlier trial recorded at the same version. Collection keeps only trials of current tasks at a version Harbor would reuse: the same version, or the same major and minor with another patch.

A regrade is a new trial in the new job that names its source trial, so collection follows that chain to the trial whose agent wrote the solution and keeps that trial's job, trial, diff, tokens, and latency with the regrade's reward and verdict, and the votes on the solution stay. Harbor can regrade one trial twice, so collection keeps one output per solution. `bench check` and `bench run` also name each rendered case whose task changed under its version, as a Harbor upgrade that changes a `task.toml` default does.

Reused trials stay in their original job, so collection reads every job directory of the family. It keeps at most `samples` outputs per case, system, and fingerprint, which is the slot Harbor reruns.

Collection requires the current agent configuration, environment, and verifier-disable setting, including the originating solution's generation inputs. It excludes unlisted systems and stale configurations. Within a current slot it prefers a successful trial and then the oldest, so an equivalent rerun keeps the original output and an errored trial drops out when a successful rerun replaces it.
