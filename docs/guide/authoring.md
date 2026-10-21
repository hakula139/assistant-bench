# Authoring

Learn how to add a family, write its cases or tasks, and declare a system under test, running `uv run bench check <family>` after each change.

## Families

A family corresponds to one directory under `families/` and one leaderboard, containing `family.toml` and exactly one task source: `cases.jsonl`, which the bench renders into one Harbor task per case, or `tasks/`, which contains Harbor task directories that run as they are.

```toml
# families/smoke-text/family.toml
scorer = "votes"
systems = ["opus-5.5", "gpt-6.1-sol"]
samples = 1
max_output_tokens = 512
```

| Field               | Meaning                                                                                                            |
| ------------------- | ------------------------------------------------------------------------------------------------------------------ |
| `scorer`            | `votes` for outputs ranked by the owner's blind pairwise votes, or `tests` for the reward of each task's own tests |
| `systems`           | The `systems.toml` tables that `bench run` sends the family to. Optional, and empty by default                     |
| `samples`           | Harbor attempts per task and system, at least 1                                                                    |
| `max_output_tokens` | The completion bound, including reasoning tokens. Required for a `cases.jsonl` family                              |

## Cases

Each line of `cases.jsonl` is one case:

```json
{
  "id": "en-greeting",
  "category": "message",
  "source": "synthetic",
  "instruction": "Write a one-sentence greeting for a new contributor to an open-source project.",
  "checklist": ["One sentence"]
}
```

- `id` is unique across the committed and local cases of the family and uses lowercase letters, digits, and hyphens. Once a case has run, an edited instruction needs a new ID, because Harbor aborts a job whose task keeps its version but changes its content. `bench check` and `bench run` compare each rendered task with the digest Harbor recorded for it and name such a case before Harbor starts, so an edit is accepted while no trial has run the case.
- `category` tags the case for per-category leaderboards.
- `source` is `synthetic` or `public` in committed files. Cases derived from transcripts (`transcript`) and held-out cases (`held-out`) belong in `$XDG_DATA_HOME/assistant-bench/cases/<family>.jsonl`, which the bench appends to the committed cases. [Data](../conventions/data.md#data-classes) covers each class.
- `checklist` is an optional list of judging hints shown beside the outputs.

## Tasks

Each `tasks/<task>/` directory follows Harbor's task layout: `task.toml` with `[task] name = "<family>/<task>"` and a `version`, `instruction.md`, `environment/Dockerfile`, `solution/solve.sh`, and `tests/test.sh`, which writes the reward to `/logs/verifier/reward.txt`. Because Harbor runs scripts in the main container through bash, the image must include it. [`families/smoke/tasks/echo/`](../../families/smoke/tasks/echo/) is the smallest example.

## Systems

[`systems.toml`](../../systems.toml) declares one system per table, keyed by a public model name plus a suffix for a prompt or effort variant:

```toml
["opus-5.5"]
model = "claude-opus-5-5"

["opus-5.5-nixos"]
model = "claude-opus-5-5"
prompt = "nixos-config:claudeCode"
```

| Key      | Meaning                                                                                                       |
| -------- | ------------------------------------------------------------------------------------------------------------- |
| `model`  | A nixos-config catalog model, which supplies the route, the default effort, and prices                        |
| `route`  | The endpoint's model ID, for a model outside the catalog. Set exactly one of `model` and `route`              |
| `price`  | For a `route`, an optional table of `input`, `output`, and `cache_read` prices in USD per million tokens      |
| `effort` | A reasoning effort the catalog accepts for the model. A model that requires one defaults to its catalog level |
| `prompt` | The system message, as `nixos-config:<client>` for a rendered instruction file                                |

Because changing a system's route, effort, or prompt changes its [fingerprint](../design/systems.md#fingerprints), give the changed system a new table name and leave the old one for its frozen results. A rename alone keeps the fingerprint but reruns the system, since Harbor reuses a trial only under the same agent config.
