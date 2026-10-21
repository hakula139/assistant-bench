# Architecture

assistant-bench limits its code to the parts Harbor lacks: resolving systems, rendering cases into Harbor tasks, the single-call agent, collecting trials into output records, the voting arena, and the ranking and reports, while Harbor schedules trials, runs containers and verifiers, retries failures, and records every job.

## Module Map

```text
src/assistant_bench/
├── cli.py                                  # The Typer `bench` command
├── jobs.py                                 # Task rendering, Harbor job configs, runs, and ingest
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
│   ├── __init__.py                         # Harness name to a Harbor agent name or import path
│   └── generate.py                         # The single-call agent
├── scoring/
│   ├── gates.py                            # Eligibility gates for voted outputs
│   └── ranking.py                          # Bradley-Terry fit, clustered bootstrap, rank spread
├── families.py                             # family.toml, gate, and case models, and the local case merge
├── catalog.py                              # Systems, fingerprints, and prices from systems.toml and the nixos-config export
└── paths.py                                # Repo and data directory locations
```

## Import Layers

Imports point downward through these layers, so a module never imports one above it:

1. `cli`
2. `jobs`, `revisions`, and `report`
3. `arena`
4. `scoring`, `harnesses`, and `outputs`
5. `families` and `catalog`
6. `paths`

`families` and `outputs` are separated from `jobs` so that the arena and the reports read family settings and output records without the Harbor job code. A module becomes a subpackage only once it holds several members along one axis, as `harnesses/` does for agents and `scoring/` does for the ways outputs are scored.

## Families and Scorers

A family directory holds only data: `family.toml` and either `cases.jsonl` or `tasks/`, with `load_family` inferring the source from whichever exists. In `family.toml`, `scorer` discriminates the settings model, and each place that depends on the scorer matches on that model and ends in `assert_never`, so mypy reports any scorer that a `match` misses:

| Scorer  | Task source     | Verifier | Scored at ingest                                          |
| ------- | --------------- | -------- | --------------------------------------------------------- |
| `votes` | `cases.jsonl`   | Disabled | `gate_failures` from the family's gates, while votes rank |
| `tests` | `tasks/<task>/` | Enabled  | `reward`, the task's reward from Harbor                   |

## Data Flow

1. `bench run <family>` renders each case into a Harbor task under the data directory, with `instruction.md` and a `task.toml` naming a stock image, builds a job config with one agent per system listed in `family.toml`, and runs it through the Harbor CLI with telemetry off.
2. Harbor runs each trial's agent, where `Generate` sends one chat-completions request from the host, writes the reply to `output.txt` in the trial's agent logs, and records tokens, latency, the finish reason, the response model, the request ID, and the nixos-config rev on the trial's `AgentContext`.
3. `bench ingest <family>` reads the trials of the family's jobs into `outputs/<family>.jsonl`, pricing tokens from the catalog, running the gates when the scorer is `votes`, and taking the reward when it is `tests`.
4. `bench serve` serves the vote page for every family scored by votes that has outputs, and the [arena](arena.md) records each vote in `votes.sqlite` in the data directory.
5. `bench close <family>` fits the [ranking](ranking.md) over the family's votes on its current outputs, or aggregates a `tests` family's rewards, and writes the [report](reports.md) to `reports/<family>/<round>.json`.

`bench check <family>` validates a family, and for a `tests` family it also runs Harbor's oracle on every task, placing its jobs under `checks/` so that ingest never reads them.

## Reruns

Each `bench run` job lists the family's earlier jobs as diff sources, so Harbor skips every trial that an earlier job completed with the same task version and agent config, and adding a system to `family.toml` and rerunning therefore generates only the new system's trials. Every rendered task carries the version in `jobs.TASK_VERSION`. Harbor aborts a job when a task keeps its version but changes its content, so an edited case needs a new ID. `bench check` and `bench run` render the tasks into a staging directory and compare each one's content hash with the task digest Harbor recorded in the lock of every earlier trial of that task and version, so they name the changed cases before Harbor starts. They install the staged tasks only when none changed, so each installed task stays as its trials ran it, which the script gate's instruction exemption relies on. A change that alters every rendered task, such as a new stock image or agent timeout, bumps the patch version, which Harbor treats as compatible and keeps reusing the earlier trials.

Reused trials stay in their original job, so ingest reads every job directory of the family. It keeps at most `samples` outputs per case, system, and fingerprint, which is the slot Harbor reruns, preferring successful trials, so an errored trial drops out once a rerun replaces it.
