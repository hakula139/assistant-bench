# Architecture

assistant-bench limits its code to the parts Harbor lacks: resolving systems, rendering cases into Harbor tasks, the single-call agent, and collecting trials into output records, while Harbor schedules trials, runs containers and verifiers, retries failures, and records every job.

## Module Map

```text
src/assistant_bench/
├── cli.py                                  # The Typer `bench` command
├── jobs.py                                 # Task rendering, Harbor job configs, runs, and ingest
├── harnesses/
│   ├── __init__.py                         # Harness name to a Harbor agent name or import path
│   └── generate.py                         # The single-call agent
├── families.py                             # family.toml and case models, and the local case merge
├── catalog.py                              # Systems, fingerprints, and prices from systems.toml and the nixos-config export
└── paths.py                                # Repo and data directory locations
```

## Import Layers

Imports point downward through these layers, so a module never imports one above it:

1. `cli`
2. `jobs`
3. `harnesses`
4. `families` and `catalog`
5. `paths`

`families` is separated from `jobs` so that code reading family settings needs no Harbor job code. A module becomes a subpackage only once it holds several members along one axis, as `harnesses/` does for agents.

## Families and Scorers

A family directory holds only data: `family.toml` and either `cases.jsonl` or `tasks/`, with `load_family` inferring the source from whichever exists. In `family.toml`, `scorer` discriminates the settings model, and each place that depends on the scorer matches on that model and ends in `assert_never`, so mypy reports any scorer that a `match` misses:

| Scorer  | Task source     | Verifier | Reward on each output              |
| ------- | --------------- | -------- | ---------------------------------- |
| `votes` | `cases.jsonl`   | Disabled | None, since votes rank the outputs |
| `tests` | `tasks/<task>/` | Enabled  | The task's `reward` from Harbor    |

## Data Flow

1. `bench run <family>` renders each case into a Harbor task under the data directory, with `instruction.md` and a `task.toml` naming a stock image, builds a job config with one agent per system listed in `family.toml`, and runs it through the Harbor CLI with telemetry off.
2. Harbor runs each trial's agent, where `Generate` sends one chat-completions request from the host, writes the reply to `output.txt` in the trial's agent logs, and records tokens, latency, the finish reason, the response model, the request ID, and the nixos-config rev on the trial's `AgentContext`.
3. `bench ingest <family>` reads every trial from the family's jobs into `outputs/<family>.jsonl`, pricing tokens from the catalog and taking the reward when the scorer is `tests`.

`bench check <family>` validates a family, and for a `tests` family it also runs Harbor's oracle on every task, placing its jobs under `checks/` so that ingest never reads them.

## Reruns

Each `bench run` job lists the family's finished jobs as diff sources, so Harbor skips every trial that an earlier job completed with the same task version and agent config, and adding a system to `family.toml` and rerunning therefore generates only the new system's trials. Every rendered task carries the version in `jobs.TASK_VERSION`. Harbor aborts a job when a task keeps its version but changes its content, so an edited case needs a new ID, and `bench check` and `bench run` compare each case with its rendered `instruction.md` to report one before Harbor starts. A change that alters every rendered task, such as a new stock image or agent timeout, bumps the patch version, which Harbor treats as compatible and keeps reusing the earlier trials.

Reused trials stay in their original job, so ingest reads every job directory of the family. It keeps at most `samples` outputs per case and fingerprint, preferring successful trials, so an errored trial drops out once a rerun replaces it.
