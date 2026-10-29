# Running

Learn how to point the bench at a model endpoint, check and run a family, collect its outputs, vote on them, and close a round, with the setup covered in [Quickstart](quickstart.md).

## The Model Endpoint

Every system uses its configured OpenAI-compatible Chat Completions or Responses API at the endpoint in `.env`, which `bench` loads from the working directory or a parent. Copy [`.env.example`](../../.env.example) and fill in:

| Variable                  | Value                                                                                                                                                                  |
| ------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `BENCH_API_BASE_URL`      | The endpoint's base URL, including `/v1`                                                                                                                               |
| `BENCH_API_KEY_FILE`      | A file holding the API key, so the key is never copied into the checkout                                                                                               |
| `BENCH_API_CA_FILE`       | Optional. A CA bundle for an endpoint whose certificate the system store does not trust                                                                                |
| `BENCH_API_HEADERS`       | Optional. A JSON object of extra request headers, such as `{"x-example": "value"}`                                                                                     |
| `BENCH_API_LOCAL_ADDRESS` | Optional. A local IP address to bind outgoing connections to, such as `0.0.0.0` to keep them on IPv4. A bound client connects directly and ignores the proxy variables |

Without `BENCH_API_LOCAL_ADDRESS`, the client honors `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, and `NO_PROXY`.

Each system's route is the model ID the endpoint expects. Requests use the system's resolved completion allowance, including reasoning, and its selected effort through the configured API. A case trial sends the instruction as the user message and the optional system prompt. A task trial uses the family's harness to drive the task's container.

## Checking a Family

```bash
uv run bench check
uv run bench check smoke-text
```

`bench check` validates each named family, or every family when none is named. It validates `family.toml` and its systems. For a `cases.toml` family, it validates the cases and renders them into Harbor tasks, which needs neither Docker nor an endpoint. For a `tasks/` family, it also runs each task's reference solution and a do-nothing agent, as [Authoring](authoring.md#tasks) describes, which needs Docker but no endpoint.

## Running a Family

```bash
uv run bench run
uv run bench run smoke-text
uv run bench run --system <name>
```

`bench run` renders each case into a Harbor task, or checks each task of a task family as `bench check` does without running it, writes a job with one agent for each system listed in `family.toml`, and runs it through the Harbor CLI.

A run without a family sends every family that lists systems, naming families limits it to those, and `--system` alone sends the named systems to every family that lists them, failing only when no family lists one.

Each case and system pair runs `samples` times, retrying a failed trial once. A reply cut off by the completion allowance remains a recorded incomplete output and fails the completion gate. It is never retried to select a better answer. An HTTP error fails the trial. Case trials start no container and need no Docker.

On a task family, the harness runs until completion or the task's operational timeout. The task's tests then grade `/app` in a fresh container, which needs Docker. Harbor reruns an errored trial on the next run. Until a rerun succeeds, a trial whose agent timed out or failed keeps its tests' verdict, while one the tests never graded has none.

When Harbor exits, `bench run` collects the family's trials into `outputs/<family>.jsonl` and prints the summary that [Collecting Outputs](#collecting-outputs) describes, also when Harbor fails, so finished trials land before the command exits.

Because a rerun generates only the trials that no earlier job of the family finished, adding a system to `family.toml` and rerunning sends requests for that system alone.

An edited task reruns, regrades, or reuses its trials by its version bump, as [Authoring](authoring.md#tasks) lists. Editing a case's instruction reruns it under its ID on every system, and collection keeps the current trials. Publication proves the selected source and output identities. Earlier releases and saved evidence retain their frozen content.

Moving `jobs/<family>/` out of the data directory makes the next run regenerate that family's trials. Publishing the resulting outputs requires a new reviewed release.

Removing a system from the family's `systems` stops new trials and current collection. Closed reports keep their frozen identities, and a removed `reference` needs a replacement for later rounds.

A run refuses a system whose earlier trials or closed reports recorded another fingerprint, naming the recorded and current ones, since that would leave two fingerprints under one name, which `bench close` refuses. [Systems](../design/systems.md#fingerprints) and [Authoring](authoring.md#systems) describe the fix.

Adding a system to the catalog is its table plus one line in each family's `systems`, then `uv run bench run --system <name>`. Adding cases is their `[[cases]]` entries in `cases.toml`, then, once they are [reviewed](authoring.md), `uv run bench run`.

## Collecting Outputs

```bash
uv run bench collect
uv run bench collect smoke-text
```

`bench run` collects its own trials, and closing a `tests` family re-collects its trials. `bench collect` re-collects on demand for every family with at least one job, or for the named families, and prints the summary. Voted reports read the measurements from these records.

It reads the family's trials into `outputs/<family>.jsonl`, keeping at most `samples` records per case, system, and fingerprint. Which trial fills each slot follows [Architecture](../design/architecture.md#reruns).

The summary counts the records, the errors by type, the gate failures by gate for a `votes` family, and the mean reward when the family has one. For a task family it also names the tasks that no system solved, which are suspects for a wrong specification or a task too hard to rank by, and the Harbor versions when the outputs span several.

When the family lists systems, each system's verifier pass rate on tasks its own vendor authored appears beside its rate on other vendors' tasks, with a reward of at least 1 counting as a pass and an ungraded trial as a failure.

Each record holds:

| Field                                                                                      | Holds                                                                                                                                                                                             |
| ------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `trial`, `job`                                                                             | The Harbor trial and job the record came from                                                                                                                                                     |
| `case`, `system`                                                                           | The case, and the system's `systems.toml` name                                                                                                                                                    |
| `fingerprint`, `nixos_config_rev`                                                          | The system's fingerprint and the export revision it ran under                                                                                                                                     |
| `harness_version`                                                                          | The Harbor version whose terminal agent wrote a task's solution                                                                                                                                   |
| `text`                                                                                     | A case's reply, or on a voted task the git-style diff of the graded `/app` against the task's `environment/app/`, without `.git` or tool caches such as `__pycache__`                             |
| `reward`                                                                                   | A task's reward from its tests                                                                                                                                                                    |
| `gate_failures`                                                                            | The [gates](../design/families/writing.md#gate-verdicts) a voted output failed, which on a task is `tests` when its reward is below 1                                                             |
| `error`                                                                                    | The error type of a failed trial. A graded task can retain its diff and gate verdict after an agent error                                                                                         |
| `finish_reason`                                                                            | The endpoint's finish reason for a single-call reply, or for the completion failure that ended a terminal agent's run, such as `refusal` or `pause_turn`, null otherwise                          |
| `stop_details`                                                                             | The `type`, `category`, and `explanation` that the Messages API gave for the refusal that ended the trial, whether a single-call reply or a terminal agent's request, null otherwise              |
| `input_tokens`, `cached_tokens`, `cache_write_tokens`, `output_tokens`, `reasoning_tokens` | Token counts summed over the trial's requests, null unless every request reports valid usage. An unreported cache count adds 0, while an unreported reasoning count makes `reasoning_tokens` null |
| `cost_usd`                                                                                 | The cost at catalog prices, null when the catalog channel has no price                                                                                                                            |
| `latency_sec`                                                                              | The end-to-end latency, or a terminal agent's execution time                                                                                                                                      |
| `response_model`, `request_id`                                                             | The model that answered a single-call reply, and the endpoint's request ID                                                                                                                        |

## Voting

Open [bench.hakula.xyz/vote/](https://bench.hakula.xyz/vote/) and choose an available family. Every device records real votes in the same production D1 database. Staging uses a separate D1 database to validate publications and runtime changes. Until the voting Worker launches, the vote page still serves its starter round from the older Worker's databases, so a publication becomes votable only once that launch switches the page to the vote stores below.

Publishing a family's outputs for voting needs `public_voting = true` in its `family.toml`, and `BENCH_D1_TOKEN_FILE` in `.env` naming a file that holds a Cloudflare API token with D1 Edit:

```bash
uv run bench arena prepare writing-zh
uv run bench arena publish staging <publication>.json
uv run bench arena publish production <publication>.json
uv run bench arena status production writing-zh
```

`prepare` writes the publication of the family's current outputs and a Markdown listing beside it, and prints both paths. Review the listing, since publishing that exact file approves it. Publishing again with the same file changes nothing, and an interrupted publish completes when rerun. [Arena](../design/arena.md#publication-and-closing) describes what a publication holds and which cases each store votes on.

### Setting Up a Vote Store

`web/wrangler.toml` names each target's database, and `web/migrations/` holds its schema. Create a missing database, record the `database_id` it prints under the target's `[[env.<target>.d1_databases]]` entry, then apply the migrations:

```bash
pnpm --filter web exec wrangler d1 create assistant-bench-arena-staging
pnpm --filter web exec wrangler d1 migrations apply DB --env staging --remote
```

Apply the migrations again after adding one, which skips the files already applied. Until the voting Worker launches, a schema change edits `0001_init.sql` instead, so recreate each database, dropping its votes: delete it with `pnpm --filter web exec wrangler d1 delete <name>`, create it again, record its new ID, and apply the migrations.

A session ends when no eligible case remains, and each vote saves as you submit it. [Voting](voting.md) covers eligibility, keys, and issues, and [Arena](../design/arena.md) covers how pairs are chosen.

## Closing a Round

```bash
uv run bench close writing-zh
uv run bench close writing-zh --snapshot <snapshot-path>
```

For a voted family, `bench close` saves the production votes that can no longer be undone under `votes/<family>/` in the data directory, fits them, and writes `reports/<family>/<round>.json`, dated by the UTC day. `--snapshot` recloses a saved file without reading the store. For a `tests` family, `bench close <family>` collects its trials and aggregates rewards. Commit the report through a PR. [Reports](../design/reports.md) describes its contents.

## The Data Directory

Everything a run produces stays out of Git in the data directory, which `BENCH_DATA_DIR` names and which defaults to `$XDG_DATA_HOME/assistant-bench/`, or `~/.local/share/assistant-bench/`:

```text
assistant-bench/
├── cases/<family>.toml                     # Local cases, appended to the committed ones
├── cases/<family>/<task>/                  # Local tasks, beside the committed ones
├── checks/<family>/<job>/                  # Oracle and nop jobs from `bench check`
├── jobs/<family>/<job>/                    # Harbor job directories, each with its config.json and one directory per trial
├── outputs/<family>.jsonl                  # Collected Harbor trial records
├── publications/<family>/<id>.json         # Publications from `bench arena prepare`, each with its .md listing
├── tasks/<family>/<case>/                  # Harbor tasks rendered from the cases
└── votes/<family>/<taken-at>.json          # Vote cuts that `bench close` saved
```

Run `harbor view` on a family's job directory to browse its trials.
