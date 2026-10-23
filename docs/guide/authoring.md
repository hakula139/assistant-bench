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

| Field               | Required      | Meaning                                                                                                                                                                                                                                                                     |
| ------------------- | ------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `scorer`            | yes           | `votes` for outputs ranked by the owner's blind pairwise votes, or `tests` for the reward of each task's own tests, which only a `tasks/` family has                                                                                                                        |
| `aspect`            | public        | The skill the family measures, one of the IDs in `web/src/lib/aspects.ts`, such as `writing`. A public family's ID then starts with the aspect, as in `writing-zh`                                                                                                          |
| `display_name`      | yes           | The family's short name within its aspect, such as `English`, or its whole name without an aspect. Its full name, the aspect's title then the short name as in `Writing · English`, is unique across families                                                               |
| `systems`           | no            | The `systems.toml` tables that `bench run` sends the family to. Empty by default                                                                                                                                                                                            |
| `samples`           | yes           | Harbor attempts per task and system, at least 1                                                                                                                                                                                                                             |
| `max_output_tokens` | case families | The completion bound, including reasoning tokens. Changing it reruns every trial of the family and replaces its outputs, while raising `samples` runs only the extra attempts                                                                                               |
| `categories`        | no            | The categories the family's cases may carry, which reports publish. Every case's category must appear here                                                                                                                                                                  |
| `public`            | no            | Whether the public site lists the family, its systems, and its reports. `false` by default, so test families stay off the site                                                                                                                                              |
| `reference`         | votes         | One of `systems`, whose score anchors the board at 1,000 when a round ranks it                                                                                                                                                                                              |
| `render`            | no            | For `votes`, how the vote page shows outputs: `text`, the default, or `html` in a sandboxed frame                                                                                                                                                                           |
| `language`          | no            | For `votes`, `zh` or `en`, the language the `language` gate requires. Another language needs a detector in `scoring/gates.py`, the `Language` type in `families.py`, the `language` enum in `web/src/lib/family.ts`, and an entry in `languages` in `web/src/lib/method.ts` |
| `[gates]`           | no            | For `votes`, the optional `min_chars`, `max_chars`, and `script = "hans"` gates, which [Writing](../design/families/writing.md#gates) defines                                                                                                                               |

## Cases

Each line of `cases.jsonl` is one case:

```json
{
  "id": "greeting",
  "category": "message",
  "source": "synthetic",
  "instruction": "Write a one-sentence greeting for a new contributor to an open-source project.",
  "checklist": ["One sentence"]
}
```

- `id` is a slug of the scenario alone, such as `starship-bump`, unique across the committed and local cases of the family and using lowercase letters, digits, and hyphens. It omits the language, which the family fixes, and the category, since moving a case to another category would then change its ID, which gives Harbor a new task name and reruns the case on every system the family lists. A case keeps its ID when its text is edited and reruns on the next run. [Running](running.md#running-a-family) describes what happens to its earlier outputs.
- `category` tags the case for per-category leaderboards and is one of the family's `categories`, so a held-out case cannot publish a new category name. A family's page under [`design/families/`](../design/families/) describes its categories.
- `source` is `synthetic` or `public` in committed files. Cases derived from transcripts (`transcript`) and held-out cases (`held-out`) belong in `cases/<family>.jsonl` under the [data directory](running.md#the-data-directory), which the bench appends to the committed cases. [Data](../conventions/data.md#data-classes) covers each class.
- `checklist` is an optional list of points the vote page shows under the prompt as What to check, which the model never sees.

## Tasks

Each `tasks/<task>/` directory follows Harbor's task layout: `task.toml` with `[task] name = "<family>/<task>"` and a `version`, `instruction.md`, `environment/Dockerfile`, `solution/solve.sh`, and `tests/test.sh`, which writes the reward to `/logs/verifier/reward.txt`. Harbor runs the task's scripts through bash in the container that runs them, which is the verifier's own container when the verifier is separate, so the image must include bash. [`families/smoke/tasks/echo/`](../../families/smoke/tasks/echo/) is the smallest example.

A task's directory name is its ID, unique across the family's committed and local tasks, and its instruction is `instruction.md`. Its `[metadata]` carries `category`, one of the family's `categories`, and `source`, one of the [Data](../conventions/data.md#data-classes) classes.

`bench check` checks each task's `task.toml` for what a run needs:

- `[task] version`, which Harbor compares with an earlier trial's lock
- `[environment] network_mode = "no-network"`, `cpus`, and `memory_mb`
- `no-network` in the agent and verifier phases, which `[agent] network_mode` and `[verifier] network_mode` may not reopen
- `[verifier] environment_mode = "separate"`, which gives the verifier its own container
- no `[verifier.environment]`, so the verifier uses the task's own image
- no `[[steps]]`, whose per-step overrides the check does not prove
- `[agent] timeout_sec`
- `/app` under `[[artifacts]]`, which the verifier receives

It then runs Harbor's oracle and nop agents three times on each task, and fails unless every oracle attempt earns reward 1 and every nop attempt earns 0, so the reference solution passes a task that the repository as committed fails.

`bench run` runs only case families, so `bench check` validates a task family but nothing produces outputs for `bench close` ([#47](https://github.com/hakula139/assistant-bench/issues/47)).

A task family scored by `votes` cannot pass `bench check` yet, since the bench reads the verifier's reward only for a family scored by `tests`.

A local task is a directory under the data directory's `cases/<family>/`, which `bench check` reads beside the committed tasks. It carries `source = "held-out"` or `"transcript"`, as [Data](../conventions/data.md#data-classes) describes.

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

When the catalog moves a model key to a new snapshot, keep the old name on its old resolution:

1. Run `uv run bench fingerprints <base>`, which names each system whose fingerprint changed and prints the `route` table that `<base>` resolved it to from that revision's catalog. A system with a `prompt` cannot be pinned this way, so the command prints only a notice for it.
2. Replace the old name's table with the printed table, which pins its `route`, `effort`, `price`, and `display_name` and sets no `model`. The pinned `price` matters because ingest prices each output from the current system table, so a pin without it leaves the frozen outputs unpriced and out of the cost tie-break.
3. Add the new snapshot in a table whose key ends in the snapshot, as `deepseek-v4.1-flash-1017` does, and set a `display_name` that tells it apart from the pinned table.
4. Replace the old name with the new one in each family's `systems` list, including `reference` where it named the old one. Until the new snapshot collects votes, `bench close` anchors the board at the most-voted system and records it as the round's `anchor`.
5. Run `uv run bench fingerprints <base>` again to confirm that no system keeps a name under another fingerprint.
