# Authoring

Learn how to add a family, write its cases or tasks, and declare a system under test, running `uv run bench check <family>` after each change.

Have new or edited cases and tasks reviewed before any paid run over them, even when the run's budget is already approved.

## Families

A family corresponds to one directory under `families/` and one leaderboard, containing `family.toml` and exactly one task source: `cases.toml`, which the bench renders into one Harbor task per case, or `tasks/`, which contains Harbor task directories that run as they are. A minimal votes family reads:

```toml
scorer = "votes"
display_name = "Smoke Test · Text"
systems = ["opus-5.5-high", "gemini-3.8-flash-high"]
reference = "gemini-3.8-flash-high"
samples = 1
categories = ["message"]
```

Choose the scorer for the evidence the family collects: `votes` for blind comparisons, `tests` for task rewards, or `checkers` for deterministic format checks. The [family model](../../src/assistant_bench/families.py) defines accepted fields and defaults.

A public family needs a declared aspect and an ID beginning with that aspect. Its short display name becomes the full name when it has no aspect, and full names must be unique. `public` lists results on the site, while `public_voting` separately permits [production voting](../design/arena.md#publication-and-closing).

List runnable systems and a reference for voted rankings. The reference anchors a round at 1,000 when it ranks. Increasing `samples` runs only the extra attempts. Checker families require one sample and put every listed system in exactly one bare / prompted pair. Task families set a default harness, and changing it replaces the affected trials. [Running](running.md#spend-and-budgets) explains budget admission.

Keep categories in board order and defects in presentation order. Category IDs appear in reports, with labels supplied by the site glossary. Omitting defects hides issue tagging. [Writing](../design/families/writing.md#gates) defines the optional language, length and script gates.

Cases render as text by default or as HTML with `render = "html"`. Voted tasks require `render = "diff"` to show solutions against `environment/app/`. An HTML reply must be a complete self-contained document, bare or inside one complete HTML fence with no surrounding prose:

- Include explicit doctype, html, head and body boundaries. Collection preserves the extracted document's bytes and records no text length.
- Use inline CSS, data images or fonts, native controls, static SVG and CSS animation. JavaScript, external resources, form submission and persistence are unavailable.
- Omit resource hints, refresh or DNS-enable metadata, base elements, SVG animation elements and navigation outside document fragments.

Each `checkers` pair is `{ bare = "<system>", prompted = "<system>" }`. The bare system has no system prompt, and the prompted system has one. Their resolved generation settings must otherwise match, including model, API, effort, context, completion limit, harness and generation version. Checker families require cases and use each case's language.

## Cases

Each `[[cases]]` entry of `cases.toml` is one case. An empty document declares `cases = []`:

```toml
[[cases]]
id = "greeting"
category = "message"
source = "synthetic"
instruction = '''
Write a one-sentence greeting for a new contributor to an open-source project.
'''
checklist = ["One sentence"]
```

- `id` is a slug of the scenario alone, unique across the family's public and private cases and using lowercase letters, digits, and hyphens. It omits the language and category, so a case can move between categories without a new ID, which would give Harbor a new task name and rerun the case on every listed system. Editing the instruction keeps the ID and [reruns](../design/architecture.md#reruns) the case.
- `category` tags the case for per-category leaderboards and is one of the family's `categories`, so a held-out case cannot publish a new category name. The site shows its label from the glossary. A family's page under [`design/families/`](../design/families/) describes its categories.
- `source` is `synthetic` or `public` in this repository. Cases derived from transcripts (`transcript`) and held-out cases (`held-out`) belong in `cases/<family>.toml` in the private repository, which runtime commands read at a pinned revision beside the public cases. [Data](../conventions/data.md#data-classes) covers each class.
- `instruction` is the prompt every system receives as Markdown. Write every instruction as a TOML multi-line literal string, with `'''` and a line break opening it and `'''` alone on the closing line, so it ends in exactly one newline and the source shows the prompt byte for byte, with no escapes. The instruction follows the repository's Markdown rules like any other Markdown file, with chat logs, transcripts, and other material whose lines Markdown would join or garble in a fenced code block.
- `checklist` is an optional list of points the vote page shows under the prompt as What to check, which the model never sees.
- `[cases.checks]` belongs only to checker cases. Declare the language, prose or directory-tree kind, applicable format rules and any exact quoted-input exemptions. Coverage targets select a subset without changing applicability. A forbidden-marker check names the planted instruction substring. Keep this metadata separate from the instruction, so editing it recollects verdicts without generating responses again.

Writing cases follow the family's [drafting and sourcing policy](../design/families/writing.md#case-sourcing).

## Tasks

Each `tasks/<task>/` directory follows Harbor's task layout: `task.toml` with `[task] name = "<family>/<task>"` and a `version`, `instruction.md`, `environment/Dockerfile`, `solution/solve.sh`, and `tests/test.sh`, which writes the reward to `/logs/verifier/reward.txt`. The image must include bash, as [Harbor](../research/harbor.md#quirks-of-the-pinned-release) explains. [`families/smoke/tasks/echo/`](../../families/smoke/tasks/echo/) is the smallest example.

A task's directory name is its ID, unique across the family's public and private tasks, and its instruction is `instruction.md`. Its `[metadata]` carries `category`, one of the family's `categories`, and `source`, one of the [Data](../conventions/data.md#data-classes) classes. A family that lists systems also requires `author`, a free-form label naming the model that wrote the task, and `author_vendor`, its vendor ID from the shared catalog. Authorship remains fixed when that system retires from the runnable configuration.

Tasks need a semantic version, explicit CPU, memory and agent timeout limits, and no network for either phase. The verifier uses a separate container from the same task image and receives `/app` as an artifact. Per-step overrides and a separate verifier image are unsupported.

`bench check` validates these limits and runs the reference solution (`oracle`) and do-nothing agent (`nop`). The reference must earn reward 1 and the starter reward 0.

Once a task has run, every edit to it needs a bump of `[task] version`, since Harbor aborts a job whose task changed under a version an earlier trial recorded, and `bench check` and `bench run` name such a task first:

| Change                                                            | Bump  | Earlier trials                                                                                                     |
| ----------------------------------------------------------------- | ----- | ------------------------------------------------------------------------------------------------------------------ |
| One no trial depends on, such as `solution/`                      | Patch | Reused                                                                                                             |
| `tests/` alone                                                    | Minor | Regraded from their recorded `/app` with no model call, keeping the votes on pairs whose solutions both still pass |
| `instruction.md`, `environment/`, or anything else the agent sees | Major | Rerun on every system, dropping the votes on the old solutions                                                     |

Deleting a task's directory retires it, after which collection leaves its trials out.

A private task is a directory under the private repository's `cases/<family>/`, which `bench check --private` reads beside the committed tasks. It carries `source = "held-out"` or `"transcript"`, as [Data](../conventions/data.md#data-classes) describes.

## Systems

[`systems.toml`](../../systems.toml) declares one system per table, keyed by a public model name plus a suffix for a prompt or effort variant. When one model name has covered several snapshots, the key includes the snapshot, as in `deepseek-v4.1-flash-1017-high`:

```toml
["opus-5.5-high"]
model = "claude-opus-5-5"
api = "anthropic-messages"
effort = "high"
display_name = "Claude Opus 5.5 · High"

["opus-5.5-nixos"]
model = "claude-opus-5-5"
api = "anthropic-messages"
effort = "high"
prompt = "nixos-config:claudeCode"
display_name = "Claude Opus 5.5 · nixos-config"
```

| Key            | Meaning                                                                                                                                 |
| -------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| `model`        | A nixos-config catalog model, which supplies the route, vendor, capacities, thinking mode, default effort, and prices                   |
| `api`          | `openai-chat-completions` by default, `openai-responses`, or `anthropic-messages`. The catalog must support the selected API and effort |
| `effort`       | A reasoning effort the catalog accepts for the model. A model that requires one defaults to its catalog level                           |
| `prompt`       | The system message, as `nixos-config:<client>` for a rendered instruction file                                                          |
| `harness`      | An installed agent override, `claude-code` for native Messages or `codex` for Responses. Requires `version` and a task family           |
| `version`      | The installed CLI's pinned `major.minor.patch` release. Requires `harness`                                                              |
| `display_name` | The required public name that the site and reports show                                                                                 |

The site shows the display name, while URLs and outputs keep the table name. Closed reports freeze their own public labels and fingerprints. Display names must be unique within the catalog and within each report, so variants and snapshots need distinct labels.

Installed agents use table names of the form `<model>-<harness>-<effort>`, and their display names include the model and harness. They cannot accept `prompt`. Their task images need the libraries the vendor's Linux executable requires, and the host needs access to the vendor's public release downloads.

[Systems](../design/systems.md#fingerprints) decides whether a system whose fingerprint changes keeps its name. A rename alone keeps the fingerprint but reruns the system, since Harbor reuses a trial only under the same agent config.

When a change moves fingerprints:

1. Run `uv run bench fingerprints`, which fails on each changed system whose closed reports froze its earlier fingerprint.
2. Give each changed system that a closed report names a new table name with a suffix identifying its effort or snapshot and a distinct `display_name`.
3. Replace the old name in each family's `systems` list and `reference` where applicable. `bench close` anchors the board at the new reference once it ranks, and until then uses the most-voted system as the round's `anchor`.
4. Preserve valid closed reports and task authorship for retired systems, and remove their runnable tables.
5. Run `uv run pytest tests/test_repository.py -k manifest --update-fixtures` to rewrite `fingerprints.json`, then `uv run bench fingerprints` to confirm that it passes.
