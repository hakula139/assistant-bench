# Authoring

Learn how to add a family, write its cases or tasks, and declare a system under test, running `uv run bench check <family>` after each change.

## Families

A family corresponds to one directory under `families/` and one leaderboard, containing `family.toml` and exactly one task source: `cases.jsonl`, which the bench renders into one Harbor task per case, or `tasks/`, which contains Harbor task directories that run as they are.

```toml
scorer = "votes"
display_name = "Smoke Test · Text"
systems = ["opus-5.5", "gpt-6.1-sol"]
reference = "gpt-6.1-sol"
samples = 1
max_output_tokens = 512
categories = ["message"]
```

| Field               | Meaning                                                                                                                                                                                                                                                                                        |
| ------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `scorer`            | `votes` for outputs ranked by the owner's blind pairwise votes, or `tests` for the reward of each task's own tests, which only a `tasks/` family has                                                                                                                                           |
| `aspect`            | The skill the family measures, one of the IDs in `web/src/lib/aspects.ts`, such as `writing`. Required for a public family                                                                                                                                                                     |
| `display_name`      | The family's short name within its aspect, such as `English`, or its whole name without an aspect. Its full name, the aspect's title then the short name as in `Writing · English`, is unique across families, and the board's URL ends in the short name, as in `/writing/english/`. Required |
| `systems`           | The `systems.toml` tables that `bench run` sends the family to. Optional, and empty by default                                                                                                                                                                                                 |
| `samples`           | Harbor attempts per task and system, at least 1                                                                                                                                                                                                                                                |
| `max_output_tokens` | The completion bound, including reasoning tokens. Required for a `cases.jsonl` family                                                                                                                                                                                                          |
| `categories`        | The categories the family's cases may carry, which reports publish. Every case's category must appear here                                                                                                                                                                                     |
| `public`            | Whether the public site lists the family, its systems, and its reports. `false` by default, so test families stay off the site                                                                                                                                                                 |
| `reference`         | For `votes`, one of `systems`, whose score anchors the board at 1000 when a round ranks it. Required                                                                                                                                                                                           |
| `render`            | For `votes`, how the vote page shows outputs: `text`, the default, or `html` in a sandboxed frame                                                                                                                                                                                              |
| `language`          | For `votes`, `zh` or `en`, the language the `language` gate requires. Optional                                                                                                                                                                                                                 |
| `[gates]`           | For `votes`, the optional `min_chars`, `max_chars`, and `script = "hans"` gates, which [Prose](../design/families/prose.md#gates) defines                                                                                                                                                      |

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
- `category` tags the case for per-category leaderboards and is one of the family's `categories`, so a held-out case cannot publish a new category name. A family's page under [`design/families/`](../design/families/) describes its categories.
- `source` is `synthetic` or `public` in committed files. Cases derived from transcripts (`transcript`) and held-out cases (`held-out`) belong in `cases/<family>.jsonl` under the [data directory](running.md#the-data-directory), which the bench appends to the committed cases. [Data](../conventions/data.md#data-classes) covers each class.
- `checklist` is an optional list of judging hints shown beside the outputs.

## Tasks

Each `tasks/<task>/` directory follows Harbor's task layout: `task.toml` with `[task] name = "<family>/<task>"` and a `version`, `instruction.md`, `environment/Dockerfile`, `solution/solve.sh`, and `tests/test.sh`, which writes the reward to `/logs/verifier/reward.txt`. Because Harbor runs scripts in the main container through bash, the image must include it. [`families/smoke/tasks/echo/`](../../families/smoke/tasks/echo/) is the smallest example.

## Systems

[`systems.toml`](../../systems.toml) declares one system per table, keyed by a public model name plus a suffix for a prompt or effort variant. When one model name has covered several snapshots, the key ends in the snapshot, as in `deepseek-v4.1-flash-1017`:

```toml
["opus-5.5"]
model = "claude-opus-5-5"

["opus-5.5-nixos"]
model = "claude-opus-5-5"
prompt = "nixos-config:claudeCode"
display_name = "Claude Opus 5.5 · nixos-config"
```

| Key            | Meaning                                                                                                       |
| -------------- | ------------------------------------------------------------------------------------------------------------- |
| `model`        | A nixos-config catalog model, which supplies the route, the default effort, prices, and the display name      |
| `route`        | The endpoint's model ID, for a model outside the catalog. Set exactly one of `model` and `route`              |
| `price`        | For a `route`, an optional table of `input`, `output`, and `cache_read` prices in USD per million tokens      |
| `effort`       | A reasoning effort the catalog accepts for the model. A model that requires one defaults to its catalog level |
| `prompt`       | The system message, as `nixos-config:<client>` for a rendered instruction file                                |
| `display_name` | The name the site shows, in place of the catalog model's `name`. Required for a `route`                       |

Every page names a system by its display name, while URLs, outputs, and reports keep the table name. Display names must be unique across `systems.toml`, so a variant or a snapshot sets a `display_name` that tells it apart.

Because changing a system's route, effort, or prompt changes its [fingerprint](../design/systems.md#fingerprints), give the changed system a new table name and leave the old one for its frozen results. A rename alone keeps the fingerprint but reruns the system, since Harbor reuses a trial only under the same agent config.
