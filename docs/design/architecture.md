# Architecture

assistant-bench limits its code to the parts Harbor lacks: resolving systems, rendering cases into Harbor tasks, the `Generate` and `Terminus` agents, the host environment that runs `Generate` without a container, collecting trials into output records, the voting arena, and the ranking and reports, while Harbor schedules trials, runs containers and verifiers, retries failures, and records every job.

## Module Map

```text
src/assistant_bench/
├── cli.py                                  # The Typer `bench` command
├── ci.py                                   # Coding task selection and checks for Harbor Smoke
├── jobs.py                                 # Task rendering, Harbor job configs, runs, and collection
├── revisions.py                            # Systems as committed at another revision, for the fingerprint check
├── report.py                               # The report schema, one results variant per scorer, and closing a round
├── arena/
│   ├── api.py                              # The FastAPI vote API and the render origin
│   ├── service.py                          # Sessions and blinded pairs
│   ├── scheduler.py                        # Pair choice: swapped repeats, the balanced seed, adaptive pairing
│   ├── resolution.py                       # Results the outputs settle: gate failures and identical text
│   └── store.py                            # The append-only SQLite vote store
├── outputs.py                              # Output records and outputs/<family>.jsonl
├── harnesses/
│   ├── __init__.py                         # System harnesses: a Harbor agent and its builders
│   ├── endpoint.py                         # The endpoint variables and the chat-completions client
│   ├── generate.py                         # The single-call agent
│   ├── host.py                             # The Harbor environment for single-call jobs, which starts no container
│   ├── labels.py                           # The kwargs that label a trial for collection
│   ├── options.py                          # The kwargs each harness builds from a system and settings
│   └── terminus.py                         # Harbor's terminus-2 agent through the bench's endpoint
├── scoring/
│   ├── gates.py                            # Eligibility gates for voted outputs
│   └── ranking.py                          # Bradley-Terry fit, clustered bootstrap, rank spread
├── families.py                             # family.toml, gate, and case models, and the local case merge
├── catalog.py                              # Systems, fingerprints, and prices from systems.toml and the nixos-config export
├── messages.py                             # The style of author-facing messages: code spans and validation errors
├── diffs.py                                # Git-style diffs of a task's workspace against its repository
└── paths.py                                # Repo and data directory locations, and atomic file writes
```

## Import Layers

Imports point downward through these layers, so a module never imports one above it:

1. `cli`
2. `jobs`, `revisions`, and `report`
3. `arena`
4. `outputs`, `harnesses`, and `scoring`
5. `families` and `catalog`
6. `messages`, `paths`, and `diffs`

`families` and `outputs` are separated from `jobs` so that the arena and the reports read family settings and output records without the Harbor job code. A module becomes a subpackage only once it holds several members along one axis, as `harnesses/` does for agents and the environment single-call agents run in, and `scoring/` does for the ways outputs are scored.

## Families and Scorers

A family directory holds only data: `family.toml` and either `cases.jsonl` or `tasks/`, with `load_family` inferring the source from whichever exists. The source decides how trials run and what grades them:

| Source          | Agent                  | Verifier | Each output holds                                           |
| --------------- | ---------------------- | -------- | ----------------------------------------------------------- |
| `cases.jsonl`   | `generate`             | Disabled | The reply, which the family's gates check                   |
| `tasks/<task>/` | The family's `harness` | Enabled  | The task's `reward`, and the diff of its `/app` for `votes` |

In `family.toml`, `scorer` discriminates the settings model, which decides how the outputs rank, and each place that depends on the scorer matches on that model and ends in `assert_never`, so mypy reports any scorer that a `match` misses:

| Scorer  | Sources       | Ranks by                                                                                                   |
| ------- | ------------- | ---------------------------------------------------------------------------------------------------------- |
| `votes` | Both          | The owner's votes, after `gate_failures` settle each pair a gate decides: a case's gates or a task's tests |
| `tests` | `tasks/` only | The mean of each system's rewards                                                                          |

## Data Flow

1. `bench run [<family>...]` renders each case into a Harbor task under the data directory, with `instruction.md` and a `task.toml`, or checks each of a task family's own tasks, builds a job config with one agent per system listed in `family.toml` or per system that `--system` names, and runs it through the Harbor CLI with telemetry off.
2. Harbor runs each trial's agent. A `Generate` trial starts no container: `Generate` sends one chat-completions request from the host, writes the reply to `output.txt` in the trial's agent logs, and records tokens, latency, the finish reason, the response model, the request ID, and the nixos-config rev on the trial's `AgentContext`. `Terminus` drives the task's container from the host until the model completes the task or runs out of turns, and Harbor then grades the container's `/app` with the task's tests in a fresh container.
3. `bench run` collects the family's trials into `outputs/<family>.jsonl` once Harbor exits, also after a failed run, and `bench collect` re-collects them. Both price tokens from the catalog and take a task's reward. A `votes` family runs its gates over each reply, or for a task family records the diff of each graded `/app` against the task's `environment/app/` and fails the `tests` gate when the reward is below 1.
4. `bench serve` collects every family scored by votes that has jobs, then serves the vote page for each that has outputs, and the [arena](arena.md) records each vote in `votes.sqlite` in the data directory.
5. `bench close <family>` collects the family's trials, then fits the [ranking](ranking.md) over the family's votes on its current outputs, or aggregates a `tests` family's rewards, and writes the [report](reports.md) to `reports/<family>/<round>.json`.

`bench check [<family>...]` validates each named family, or every family when none is named, and for a `tasks/` family it also runs Harbor's oracle and nop on every task, placing its jobs under `checks/` so that collection never reads them.

## Reruns

Each `bench run` job lists the family's earlier jobs as diff sources, so Harbor skips every trial that an earlier job completed with the same task version and agent config, and adding a system to `family.toml` and rerunning therefore generates only the new system's trials. Each rendered task's version carries a hash of the bench's own task inputs as build metadata on `jobs.TASK_VERSION`, and Harbor reruns a task whose build metadata changed, so an edited case reruns under its ID. `bench check` and `bench run` render the tasks and install them, removing directories for cases the family no longer has. Collection keeps only trials of each case's installed task. The hash covers the instruction and every setting `render_task_config` writes, which are the task name and the agent timeout, so changing either changes the affected versions and reruns those cases. The diff also compares each trial's environment, which follows the job's harnesses as [Systems](systems.md#harnesses) describes, so changing it reruns every trial of the jobs it applies to. A Harbor upgrade that changes a `task.toml` default aborts the job, as [Harbor](../research/harbor.md#quirks-of-the-pinned-release) describes.

A task family's tasks carry the semantic `version` their authors set, which Harbor compares with each earlier trial's: a patch bump reuses the trial, a minor bump regrades its recorded `/app` in a fresh verifier with no model call, and a major bump reruns it. Harbor aborts a job whose task changed without a bump, so `bench check` and `bench run` first name each task whose content differs from what an earlier trial recorded at the same version. Collection keeps only trials of current tasks at a version Harbor would reuse: the same version, or the same major and minor with another patch. A regrade is a new trial in the new job that names its source trial, so collection follows that chain to the trial whose agent wrote the solution and keeps that trial's job, trial, diff, tokens, and latency with the regrade's reward and verdict, and the votes on the solution stay. Harbor can regrade one trial twice, so collection keeps one output per solution. `bench check` and `bench run` also name each rendered case whose task changed under its version, as a Harbor upgrade that changes a `task.toml` default does.

Reused trials stay in their original job, so collection reads every job directory of the family. It keeps at most `samples` outputs per case, system, and fingerprint, which is the slot Harbor reruns. Within a slot it prefers a successful trial whose recorded agent configuration, environment, and verifier-disable setting match the current job, then the oldest, so changed inputs replace what an earlier job produced while a rerun of the same inputs keeps the outputs its votes refer to, and an errored trial drops out once a rerun replaces it.
