# Architecture

assistant-bench limits its code to the parts Harbor lacks, which the module map lists, while Harbor schedules trials, runs containers and verifiers, and records every job.

## Module Map

```text
src/assistant_bench/
├── cli.py               # The Typer `bench` command
├── ci.py                # Coding task selection and checks for Harbor Smoke
├── task_assets.py       # Regeneration of each coding task's shared files
├── arena/
│   ├── commands.py      # Publication, status and moderation commands
│   ├── d1.py            # Each hosted target's database and credential sources
│   ├── publication.py   # Immutable reviewed publications and their conditional activation
│   └── votes.py         # Vote cuts and moderation, the snapshots that save cuts, and the round they close
├── collection.py        # Collection of a family's trials into output records
├── canaries.py          # Route probes, their baseline comparison, and independent attempt records
├── arguments.py         # The family arguments, system option, and name refusals the commands share
├── tasks.py             # Rendering of cases into Harbor tasks, checks of authored tasks, and task versions
├── spend.py             # The spend ledger of agent attempts, its pricing, and a run's projection and budget
├── jobs.py              # A family's systems, its Harbor job configs, and their runs
├── report.py            # The report schema and inputs, one results variant per scorer, and closing a round
├── outputs.py           # Output records, immutable cloud generations, fingerprints and pool conflicts
├── harnesses/
│   ├── __init__.py      # System harnesses, their agent kwargs, and fingerprints
│   ├── accounting.py    # Paid request admission and durable usage receipts
│   ├── cache.py         # Verified, pinned vendor CLI downloads
│   ├── docker.py        # Installed-agent host access within Harbor's network policy
│   ├── endpoint.py      # Endpoint variables and native SDK transports
│   ├── generate.py      # The single-call agent
│   ├── host.py          # The Harbor environment for single-call jobs, which starts no container
│   ├── installed.py     # Installed agent adapters and native trace collection
│   ├── labels.py        # The kwargs that label a trial for collection
│   ├── messages.py      # Native Messages streaming and conversation content
│   ├── native.py        # The native SDK model client, conversation, and errors Terminus shares
│   ├── options.py       # The kwargs each harness builds from a system's generation identity
│   ├── proxy.py         # Trial-scoped inference forwarding with credentials kept on the host
│   ├── responses.py     # Native SDK results and stateless conversation items
│   ├── terminus.py      # Harbor's terminus-2 agent through the bench's endpoint
│   └── usage.py         # Validated measured token counters shared by every transport
├── scoring/
│   ├── checkers.py      # Deterministic instruction checks for collected replies
│   ├── gates.py         # Eligibility gates for voted outputs
│   ├── length.py        # Shared output length measurement
│   ├── pass_rates.py    # Task-clustered pass-rate intervals, pass^k, and the paired leader test
│   ├── ranking.py       # Bradley-Terry point fit and its descriptive diagnostics
│   └── weighting.py     # Family-wide cumulative actor caps
├── families.py          # Family, gate, case and vote models with explicit pinned source roots
├── catalog.py           # Systems and prices from systems.toml and the nixos-config export
├── state.py             # Private run claims, immutable records and active collection pointers
├── artifacts.py         # Pinned private Git cases and immutable raw trial archives
├── git.py               # Repository-local environment isolation for Git subprocesses
├── runtime.py           # Command-owned materialization of pinned private sources
├── execution.py         # Harbor lifecycle claims, audited archives and settlement
├── database.py          # The SQL protocol, SQLite adapter and D1 REST client
├── generation.py        # Revision-owned generation protocol and locked native agent version
├── messages.py          # The style of author-facing messages: code spans and validation errors
├── paths.py             # Repo and data directory locations, and atomic file writes
├── diffs.py             # Git-style diffs of a task's workspace against its repository
└── html.py              # Unchanged complete HTML extraction and preview eligibility
```

## Import Layers

Imports point downward through these layers, so a module never imports one above it:

1. `cli`, `ci`, and `task_assets`
2. `arena.commands`
3. `canaries`, `collection`, `execution`, `runtime`, and `arguments`
4. `tasks` and `spend`
5. `jobs`
6. The remaining `arena` modules
7. `report`
8. `outputs`, `harnesses`, and `scoring`
9. `families`, `catalog`, and `state`
10. `artifacts`, `database`, `generation`, `git`, `messages`, `paths`, `diffs`, and `html`

`families` and `outputs` are separated from `collection`, `tasks`, and `jobs` so that the arena and the reports read family settings and output records without the Harbor job code. `report` sits below the arena modules because it owns the report inputs and the fixed votes method, which the arena builds from vote cuts. A module becomes a subpackage only once it holds several members along one axis, as `harnesses/` does for agents and the environment single-call agents run in, and `scoring/` does for the ways outputs are scored.

## Families and Scorers

A family directory holds `family.toml` and either `cases.toml` or `tasks/`, with `load_family` inferring the source from whichever exists. The coding family also holds `shared/`, the authoring source for the files that `task_assets` copies into every task. The source decides how trials run and what grades them:

| Source          | Agent                  | Verifier | Each output holds                                           |
| --------------- | ---------------------- | -------- | ----------------------------------------------------------- |
| `cases.toml`    | `generate`             | Disabled | The reply, which the family's gates check                   |
| `tasks/<task>/` | The family's `harness` | Enabled  | The task's `reward`, and the diff of its `/app` for `votes` |

In `family.toml`, `scorer` discriminates the settings model, which decides how the outputs rank, and each place that depends on the scorer matches on that model and ends in `assert_never`, so mypy reports any scorer that a `match` misses:

| Scorer     | Sources           | Ranks by                                                                                                      |
| ---------- | ----------------- | ------------------------------------------------------------------------------------------------------------- |
| `votes`    | Both              | Blind pairwise votes, after `gate_failures` settle each pair a gate decides: a case's gates or a task's tests |
| `tests`    | `tasks/` only     | The mean of each system's rewards                                                                             |
| `checkers` | `cases.toml` only | Binary case passes across every applicable format rule                                                        |

## Data Flow

The `checkers` scorer evaluates collected replies locally. Its case context, coverage targets and grader revision do not enter rendered instructions, task versions or system fingerprints. Recollection updates verdicts without generating replies, and closing requires one current verdict per configured system and case.

1. `bench run` pins the implementation and private source revisions, restores earlier artifacts into a disposable workspace, and checks tasks and budget admission before starting Harbor. Private D1 records request admissions and receipts while trials run locally.
2. A single-call trial sends one request without a container. A task agent works in its task's container with native context management and an operational timeout, then Harbor grades `/app` with the task's tests in a fresh container.
3. After Harbor exits, including a failed run, completed trial artifacts enter private Git and collection seals an output generation in private D1. `bench collect` can repeat collection from those artifacts with pinned cases, collection code and public system labels. Voted replies receive gate verdicts. Voted tasks retain the graded workspace diff and fail the `tests` gate when their reward is below 1.
4. Arena preparation saves a publication of a sealed generation for review. Publishing activates that immutable publication in the target's D1 database. Public and Access-protected private [arenas](arena.md) record votes independently of local execution, following the [Running](../guide/running.md#voting) procedure.
5. Closing a voted family seals counted votes, fits the [ranking](ranking.md) and writes its [report](reports.md). Replay uses the snapshot's category order, close time and each cut's pinned output generation. A `tests` or `checkers` family closes from its collected generation and pinned case and code revisions.

`bench check [<family>...]` validates public sources without cloud authentication, and for a `tasks/` family also runs Harbor's oracle and nop on every task in a temporary workspace. `--private` includes a pinned private repository revision and requires GitHub authentication without opening D1. Check jobs never enter collection.

## Reruns

Each `bench run` job lists the family's earlier jobs as diff sources, so Harbor skips every trial that an earlier job completed with the same task version and agent config. Adding a system therefore generates only its trials.

Each rendered task's version carries a hash of its instruction, task name and agent timeout as build metadata on `tasks.TASK_VERSION`, and Harbor reruns a task whose build metadata changed, so an edited case reruns under its ID on every system. `bench check` and `bench run` render tasks in disposable workspaces. Collection derives the expected version from its pinned case source and render settings, so stale outputs cannot become current through an old local task directory.

The diff also compares each trial's environment, which follows the job's harnesses as [Systems](systems.md#harnesses) describes, so changing it reruns every trial of the jobs it applies to. A Harbor upgrade that changes a `task.toml` default aborts the job, as [Harbor](../research/harbor.md#quirks-of-the-pinned-release) describes.

Authored tasks follow the version bumps that [Authoring](../guide/authoring.md#tasks) lists, and `bench check` and `bench run` first name each task whose content differs from what an earlier trial recorded at the same version. Collection keeps only trials of current tasks at a version Harbor would reuse: the same version, or the same major and minor with another patch.

A regrade is a new trial in the new job that names its source trial, so collection follows that chain to the trial whose agent wrote the solution and keeps that trial's job, trial, diff, tokens, and latency with the regrade's reward and verdict, and the votes on the solution stay. Harbor can regrade one trial twice, so collection keeps one output per solution. `bench check` and `bench run` also name each rendered case whose task changed under its version, as a Harbor upgrade that changes a `task.toml` default does.

Reused trials retain their original job identity, so collection restores the family's recorded job artifacts. It keeps at most `samples` outputs per case, system, and fingerprint, which is the slot Harbor reruns.

Collection requires the current agent configuration, environment, and verifier-disable setting, including the originating solution's generation inputs. It excludes unlisted systems and stale configurations. Within a current slot it prefers a successful trial, then an errored trial its tests graded, then one they never graded, and the oldest within each tier whatever its reward. Age follows job names, which `bench` sets to the job's start time to the microsecond. An equivalent rerun therefore keeps the original output, a graded agent error replaces an error that left no verdict, and an errored trial drops out when a successful rerun replaces it.
