# Authoring

Learn how to add a family, write its cases or tasks, and declare a system under test, running `uv run bench check <family>` after each change.

Have new or edited cases and tasks reviewed before any paid run over them, even when the run's budget is already approved.

## Families

A family corresponds to one directory under `families/` and one leaderboard, containing `family.toml` and exactly one task source: `cases.toml`, which the bench renders into one Harbor task per case, or `tasks/`, which contains Harbor task directories that run as they are.

```toml
scorer = "votes"
display_name = "Smoke Test · Text"
systems = ["opus-5.5-high", "gpt-6.1-sol-high"]
reference = "gpt-6.1-sol-high"
samples = 1
categories = ["message"]
```

Shared fields:

| Field          | Required        | Meaning                                                                                                                                                            |
| -------------- | --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `scorer`       | yes             | `votes` for blind pairwise votes, or `tests` for a task family's test rewards                                                                                      |
| `aspect`       | public families | The skill measured. Public families use `web/src/lib/aspects.ts` and an ID starting with that aspect, such as `writing-zh`. Private families may use any aspect ID |
| `display_name` | yes             | The short name within the aspect, or the whole name without one. The full name, such as `Writing · English`, must be unique                                        |
| `public`       | no              | Whether the site lists the family, its systems, and reports. Defaults to `false`                                                                                   |
| `systems`      | no              | The `systems.toml` tables to run. Defaults to empty                                                                                                                |
| `samples`      | yes             | Harbor attempts per task and system, at least 1                                                                                                                    |
| `categories`   | no              | The case category IDs, in board order, that its cases and task metadata may use. Reports publish the IDs, and the site labels them from its glossary               |

Vote-only fields:

| Field           | Required      | Meaning                                                                                                                                |
| --------------- | ------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| `reference`     | yes           | One of `systems`, anchored at 1,000 when the round ranks it                                                                            |
| `public_voting` | no            | Opt a rendered case family into [approved public publication](../design/arena.md#publication-and-evidence). Defaults to `false`        |
| `render`        | task families | `text` (default) or sandboxed `html` for cases. Tasks require `diff`, showing each solution against `environment/app/`                 |
| `defects`       | no            | Unique issue IDs in presentation order. Defaults to empty, hiding Issues buttons. See [Voting](voting.md#casting-a-vote) for shortcuts |
| `language`      | no            | `zh` or `en` for a case family's language gate                                                                                         |
| `[gates]`       | no            | A case family's `min_chars`, `max_chars`, and `script = "hans"` settings, defined in [Writing](../design/families/writing.md#gates)    |

A category ID comes from the `Category` literal, and the site labels it in its glossary, so a new ID updates that literal, the `categories` list in `lib/categories.ts`, and the glossary, as [TypeScript](../conventions/typescript.md#reports) describes.

Case families use `votes`. Public preparation supports rendered case families. Authored task families retain collection and tests-only regrading. [TypeScript](../conventions/typescript.md#reports) covers adding defect IDs or languages.

Task families with systems also require:

| Field     | Meaning                                                                                                  |
| --------- | -------------------------------------------------------------------------------------------------------- |
| `harness` | The agent every system uses. Currently `terminus-2`, since `generate` cannot act in a task's environment |

Case families use `generate` and set no `harness`. Systems supply their completion and context allowances. Changing the harness replaces the family's trials. Raising `samples` runs only the extra attempts.

## Cases

Each `[[cases]]` entry of `cases.toml` is one case. An empty document declares `cases = []`:

```toml
[[cases]]
id = "greeting"
category = "message"
source = "synthetic"
instruction = "Write a one-sentence greeting for a new contributor to an open-source project."
checklist = ["One sentence"]
```

- `id` is a slug of the scenario alone, such as `starship-bump`, unique across the committed and local cases of the family and using lowercase letters, digits, and hyphens. It omits the language, which the family fixes, and the category, since moving a case to another category would then change its ID, which gives Harbor a new task name and reruns the case on every system the family lists. A case keeps its ID when its instruction is edited and reruns on the next run. [Running](running.md#running-a-family) describes what happens to its earlier outputs.
- `category` tags the case for per-category leaderboards and is one of the family's `categories`, so a held-out case cannot publish a new category name. The site shows its label from the glossary. A family's page under [`design/families/`](../design/families/) describes its categories.
- `source` is `synthetic` or `public` in committed files. Cases derived from transcripts (`transcript`) and held-out cases (`held-out`) belong in `cases/<family>.toml` under the [data directory](running.md#the-data-directory), which the bench appends to the committed cases. [Data](../conventions/data.md#data-classes) covers each class.
- `instruction` is the prompt every system receives. Write a multiline instruction as a literal `'''` string, so its quotes and backslashes read as the rendered task shows them.
- `checklist` is an optional list of points the vote page shows under the prompt as What to check, which the model never sees.

## Tasks

Each `tasks/<task>/` directory follows Harbor's task layout: `task.toml` with `[task] name = "<family>/<task>"` and a `version`, `instruction.md`, `environment/Dockerfile`, `solution/solve.sh`, and `tests/test.sh`, which writes the reward to `/logs/verifier/reward.txt`. Harbor runs the task's scripts through bash in the container that runs them, which is the verifier's own container when the verifier is separate, so the image must include bash. [`families/smoke/tasks/echo/`](../../families/smoke/tasks/echo/) is the smallest example.

A task's directory name is its ID, unique across the family's committed and local tasks, and its instruction is `instruction.md`. Its `[metadata]` carries `category`, one of the family's `categories`, and `source`, one of the [Data](../conventions/data.md#data-classes) classes. A family that lists systems also requires `author`, the public label of the system that wrote the task, and `author_vendor`, its vendor ID from the shared catalog or an explicitly declared bare route. Authorship remains fixed when that system retires from the runnable configuration.

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

[`systems.toml`](../../systems.toml) declares one system per table, keyed by a public model name plus a suffix for a prompt or effort variant. When one model name has covered several snapshots, the key includes the snapshot, as in `deepseek-v4.1-flash-1017-high`:

```toml
["opus-5.5-high"]
model = "claude-opus-5-5"
api = "anthropic-messages"
effort = "high"

["opus-5.5-nixos"]
model = "claude-opus-5-5"
api = "anthropic-messages"
effort = "high"
prompt = "nixos-config:claudeCode"
display_name = "Claude Opus 5.5 · nixos-config"
```

| Key                 | Meaning                                                                                                                                 |
| ------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| `model`             | A nixos-config catalog model, which supplies the route, the default effort, prices, and the display name                                |
| `route`             | The endpoint's model ID, for a model outside the catalog. Set exactly one of `model` and `route`                                        |
| `api`               | `openai-chat-completions` by default, `openai-responses`, or `anthropic-messages`. The catalog must support the selected API and effort |
| `thinking_mode`     | For a bare native Messages route, required as `anthropic-adaptive`. Catalog models supply their thinking mode                           |
| `vendor`            | For a `route`, its model vendor. Required when a task family lists the system                                                           |
| `price`             | For a `route`, an optional table of `input`, `output`, `cache_read`, and optional `cache_write` prices in USD per million tokens        |
| `max_output_tokens` | For a `route`, its configured completion allowance, including reasoning. Catalog models supply `maxTokens`                              |
| `context_window`    | For a `route`, its configured joint input and output capacity. Catalog models supply `contextWindow`                                    |
| `effort`            | A reasoning effort the catalog accepts for the model. A model that requires one defaults to its catalog level                           |
| `prompt`            | The system message, as `nixos-config:<client>` for a rendered instruction file                                                          |
| `display_name`      | The name the site shows, in place of the catalog model's `name`. Required for a `route`                                                 |

Current catalog entries use their display names, while URLs and outputs keep the table name. Closed reports freeze their own public labels and fingerprints. Display names must be unique within the catalog and within each report, so variants and snapshots need distinct labels.

Changing a system's generation settings changes its [fingerprint](../design/systems.md#fingerprints), and whether the system keeps its name follows the rule there. A rename alone keeps the fingerprint but reruns the system, since Harbor reuses a trial only under the same agent config.

When a change moves fingerprints, whether through a model snapshot, effort, capacities, the generation protocol, or the fingerprint code:

1. Run `uv run bench fingerprints`, which fails on each changed system whose closed reports froze its earlier fingerprint.
2. Give each changed system that a closed report names a new table name with a suffix identifying its effort or snapshot and a distinct `display_name`.
3. Replace the old name in each family's `systems` list and `reference` where applicable. `bench close` anchors the board at the new reference once it ranks, and until then uses the most-voted system as the round's `anchor`.
4. Preserve valid closed reports and task authorship for retired systems, and remove their runnable tables.
5. Run `uv run pytest tests/test_repository.py -k manifest --update-fixtures` to rewrite `fingerprints.json`, then `uv run bench fingerprints` to confirm that it passes.
6. Before the next paid run, delete the stale trials of each changed system that no closed report names under `jobs/<family>/` in the data directory, so it reruns under its name.
