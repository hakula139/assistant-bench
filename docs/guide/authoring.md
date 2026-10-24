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

Shared fields:

| Field               | Required                                     | Meaning                                                                                                                                                            |
| ------------------- | -------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `scorer`            | yes                                          | `votes` for blind pairwise votes, or `tests` for a task family's test rewards                                                                                      |
| `aspect`            | public families                              | The skill measured. Public families use `web/src/lib/aspects.ts` and an ID starting with that aspect, such as `writing-zh`. Private families may use any aspect ID |
| `display_name`      | yes                                          | The short name within the aspect, or the whole name without one. The full name, such as `Writing · English`, must be unique                                        |
| `public`            | no                                           | Whether the site lists the family, its systems, and reports. Defaults to `false`                                                                                   |
| `systems`           | no                                           | The `systems.toml` tables to run. Defaults to empty                                                                                                                |
| `samples`           | yes                                          | Harbor attempts per task and system, at least 1                                                                                                                    |
| `max_output_tokens` | case families and task families with systems | The completion bound per model call, including reasoning                                                                                                           |
| `categories`        | no                                           | The allowed case categories, which reports publish                                                                                                                 |

Vote-only fields:

| Field       | Required      | Meaning                                                                                                                             |
| ----------- | ------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| `reference` | yes           | One of `systems`, anchored at 1,000 when the round ranks it                                                                         |
| `render`    | task families | `text` (default) or sandboxed `html` for cases. Tasks require `diff`, showing each solution against `environment/app/`              |
| `defects`   | no            | At most five issue IDs, in vote-page key order. Defaults to empty, hiding Issues buttons                                            |
| `language`  | no            | `zh` or `en` for a case family's language gate                                                                                      |
| `[gates]`   | no            | A case family's `min_chars`, `max_chars`, and `script = "hans"` settings, defined in [Writing](../design/families/writing.md#gates) |

Case families use `votes`. On task families scored by votes, tests settle the pairs they separate, and votes compare passing solutions. [TypeScript](../conventions/typescript.md#reports) covers adding defect IDs or languages.

Task families with systems also require:

| Field       | Meaning                                                                                                  |
| ----------- | -------------------------------------------------------------------------------------------------------- |
| `harness`   | The agent every system uses. Currently `terminus-2`, since `generate` cannot act in a task's environment |
| `max_turns` | The same agent turn budget for every system                                                              |

Case families use `generate` and set no `harness`. Changing `max_output_tokens`, `harness`, or `max_turns` replaces the family's trials. Raising `samples` runs only the extra attempts.

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

- `id` is a slug of the scenario alone, such as `starship-bump`, unique across the committed and local cases of the family and using lowercase letters, digits, and hyphens. It omits the language, which the family fixes, and the category, since moving a case to another category would then change its ID, which gives Harbor a new task name and reruns the case on every system the family lists. A case keeps its ID when its instruction is edited and reruns on the next run. [Running](running.md#running-a-family) describes what happens to its earlier outputs.
- `category` tags the case for per-category leaderboards and is one of the family's `categories`, so a held-out case cannot publish a new category name. A family's page under [`design/families/`](../design/families/) describes its categories.
- `source` is `synthetic` or `public` in committed files. Cases derived from transcripts (`transcript`) and held-out cases (`held-out`) belong in `cases/<family>.jsonl` under the [data directory](running.md#the-data-directory), which the bench appends to the committed cases. [Data](../conventions/data.md#data-classes) covers each class.
- `checklist` is an optional list of points the vote page shows under the prompt as What to check, which the model never sees.

## Tasks

Each `tasks/<task>/` directory follows Harbor's task layout: `task.toml` with `[task] name = "<family>/<task>"` and a `version`, `instruction.md`, `environment/Dockerfile`, `solution/solve.sh`, and `tests/test.sh`, which writes the reward to `/logs/verifier/reward.txt`. Harbor runs the task's scripts through bash in the container that runs them, which is the verifier's own container when the verifier is separate, so the image must include bash. [`families/smoke/tasks/echo/`](../../families/smoke/tasks/echo/) is the smallest example.

A task's directory name is its ID, unique across the family's committed and local tasks, and its instruction is `instruction.md`. Its `[metadata]` carries `category`, one of the family's `categories`, and `source`, one of the [Data](../conventions/data.md#data-classes) classes. A family that lists systems also requires `author`, the `systems.toml` name of the system that wrote the task, which may be outside the family's participants.

`bench check` checks each task's `task.toml` for what a run needs:

- `[task] version`, which Harbor compares with an earlier trial's lock
- `[environment] network_mode = "no-network"`, `cpus`, and `memory_mb`
- `no-network` in the agent and verifier phases, which `[agent] network_mode` and `[verifier] network_mode` may not reopen
- `[verifier] environment_mode = "separate"`, which gives the verifier its own container
- no `[verifier.environment]`, so the verifier uses the task's own image
- no `[[steps]]`, whose per-step overrides the check does not prove
- `[agent] timeout_sec`
- `/app` under `[[artifacts]]`, which the verifier receives

It then runs the reference solution (Harbor's `oracle` agent) and a do-nothing agent (`nop`) three times on each task, and fails unless every reference attempt earns reward 1 and every do-nothing attempt earns 0, so the reference solution passes a task that the repository as committed fails.

Once a task has run, every edit to it needs a bump of `[task] version`, since Harbor aborts a job whose task changed under a version an earlier trial recorded, and `bench check` and `bench run` name such a task first:

| Change                                                            | Bump  | Earlier trials                                                                                                     |
| ----------------------------------------------------------------- | ----- | ------------------------------------------------------------------------------------------------------------------ |
| One no trial depends on, such as `solution/`                      | Patch | Reused                                                                                                             |
| `tests/` alone                                                    | Minor | Regraded from their recorded `/app` with no model call, keeping the votes on pairs whose solutions both still pass |
| `instruction.md`, `environment/`, or anything else the agent sees | Major | Rerun on every system, dropping the votes on the old solutions                                                     |

Deleting a task's directory retires it, after which collection leaves its trials out.

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
| `vendor`       | For a `route`, its model vendor. Required when a task family lists the system or names it as an author        |
| `price`        | For a `route`, an optional table of `input`, `output`, and `cache_read` prices in USD per million tokens      |
| `effort`       | A reasoning effort the catalog accepts for the model. A model that requires one defaults to its catalog level |
| `prompt`       | The system message, as `nixos-config:<client>` for a rendered instruction file                                |
| `display_name` | The name the site shows, in place of the catalog model's `name`. Required for a `route`                       |

Current catalog entries use their display names, while URLs and outputs keep the table name. Closed reports freeze their own public labels and fingerprints. Display names must be unique within the catalog and within each report, so variants and snapshots need distinct labels.

Because changing a system's route, effort, or prompt changes its [fingerprint](../design/systems.md#fingerprints), give the changed system a new table name and leave the old one for its frozen results, unless the recipe below cannot pin its `prompt`. A rename alone keeps the fingerprint but reruns the system, since Harbor reuses a trial only under the same agent config.

When the catalog moves a model key to a new snapshot, keep the old name on its old resolution:

1. Run `uv run bench fingerprints <base>`, which names each system whose fingerprint changed and prints the `route` table that `<base>` resolved it to from that revision's catalog. A system with a `prompt` cannot be pinned this way, so the command prints only a notice for it.
2. For a system without a `prompt`, replace the old name's table with the printed table, which pins its `route`, `vendor`, `effort`, `price`, and `display_name` and sets no `model`. Collection prices outputs from this table, so keep its prices for subsequent collection.
3. Add the new snapshot in a table whose key ends in the snapshot, as `deepseek-v4.1-flash-1017` does, and set a `display_name` that tells it apart from the pinned table.
4. Replace the old name with the new one in each family's `systems` list, including `reference` where it named the old one. `bench close` anchors the board at the new snapshot once it ranks, through its votes or the results its gates and tests settle, and until then anchors it at the most-voted system and records that system as the round's `anchor`.
5. Before removing a runnable table, close a valid report while its public metadata is available. Retired outputs can enter a later report only when their known fingerprint matches that ID's latest prior report. A system with a `prompt` cannot be pinned by this recipe, so retire it after closing.
6. Run `uv run bench fingerprints <base>` again to confirm that no system keeps a name under another fingerprint.
