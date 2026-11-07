# Running

Learn how to point the bench at a model endpoint, check and run a family, collect its outputs, vote on them, and close a round, with the setup covered in [Quickstart](quickstart.md).

## The Model Endpoint

Every system calls its configured API at the endpoint in `.env`, which `bench` loads from the working directory or a parent: OpenAI-compatible Chat Completions or Responses, or the Anthropic Messages API for `api = "anthropic-messages"`. Copy [`.env.example`](../../.env.example) and fill in:

| Variable                   | Value                                                                                                                                                                  |
| -------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `BENCH_API_BASE_URL`       | The OpenAI-compatible base URL, including `/v1`                                                                                                                        |
| `BENCH_ANTHROPIC_BASE_URL` | The Messages API base URL, to which the SDK appends `/v1/messages`. Required for a Messages system                                                                     |
| `BENCH_API_KEY_FILE`       | A file holding the API key, so the key is never copied into the checkout                                                                                               |
| `BENCH_API_CA_FILE`        | Optional. A CA bundle for an endpoint whose certificate the system store does not trust                                                                                |
| `BENCH_API_HEADERS`        | Optional. A JSON object of extra request headers                                                                                                                       |
| `BENCH_API_LOCAL_ADDRESS`  | Optional. A local IP address to bind outgoing connections to, such as `0.0.0.0` to keep them on IPv4. A bound client connects directly and ignores the proxy variables |

Both APIs share the key file, CA bundle, headers, and local address.

Without `BENCH_API_LOCAL_ADDRESS`, the client honors `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, and `NO_PROXY`.

Each system's route is the model ID the endpoint expects. Requests use the system's resolved completion allowance, including reasoning, and its selected effort through the configured API. A case trial sends the instruction as the user message and the optional system prompt. A task trial uses the family's harness to drive the task's container.

## Checking a Family

```bash
uv run bench check
uv run bench check smoke-text
uv run bench check coding-repo --task <task>
```

`bench check` validates each named family, or every family when none is named, and `--task` limits one task family to the named tasks. It validates `family.toml` and its systems. For a `cases.toml` family, it validates the cases and renders them into Harbor tasks, which needs neither Docker nor an endpoint. For a `tasks/` family, it also runs each task's reference solution and a do-nothing agent, as [Authoring](authoring.md#tasks) describes, which needs Docker but no endpoint.

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

Installed agents require a named local Docker context using a Unix socket or Docker Desktop. Select it with `DOCKER_CONTEXT`. A standalone `DOCKER_HOST` and contexts using SSH or TCP endpoints are unsupported, since the model proxy runs on the client host. This is checked before CLI downloads or model calls.

When Harbor exits, `bench run` collects the family's trials into `outputs/<family>.jsonl` and prints the summary that [Collecting Outputs](#collecting-outputs) describes, also when Harbor fails, so finished trials land before the command exits.

Because a rerun generates only the trials that no earlier job of the family finished, adding a system to `family.toml` and rerunning sends requests for that system alone.

An edited task reruns, regrades, or reuses its trials by its version bump, as [Authoring](authoring.md#tasks) lists, and an edited case [reruns](../design/architecture.md#reruns) on every system. Publication proves the selected source and output identities. Earlier releases and saved evidence retain their frozen content.

Moving `jobs/<family>/` out of the data directory makes the next run regenerate that family's trials. Publishing the resulting outputs requires a new reviewed release.

Removing a system from the family's `systems` stops new trials and current collection. Closed reports keep their frozen identities, and a removed `reference` needs a replacement for later rounds.

A run refuses a system whose earlier trials or closed reports recorded another fingerprint, as [Systems](../design/systems.md#fingerprints) describes, and [Authoring](authoring.md#systems) gives the fix.

Adding a system to the catalog is its table plus one line in each family's `systems`, then `uv run bench run --system <name>`. Adding cases is their `[[cases]]` entries in `cases.toml`, then, once they are [reviewed](authoring.md), `uv run bench run`.

## Collecting Outputs

```bash
uv run bench collect
uv run bench collect smoke-text
```

`bench run` collects its own trials, and closing a `tests` family re-collects its trials. `bench collect` re-collects on demand for every family with at least one job, or for the named families, and prints the summary. Voted reports read the measurements from these records.

It reads the family's trials into `outputs/<family>.jsonl`, keeping at most `samples` records per case, system, and fingerprint. Which trial fills each slot follows [Architecture](../design/architecture.md#reruns).

The summary counts the records, the errors by type, the gate failures by gate for a `votes` family, and the mean reward when the family has one. For a task family it also names the tasks that no system solved, which are suspects for a wrong specification or a task too hard to rank by, and the Harbor versions when the outputs span several.

When the family lists systems, each system's verifier [pass rate](../design/ranking.md#test-pass-rates) on tasks its own vendor authored appears beside its rate on other vendors' tasks.

Each record holds:

| Field                                                                                      | Holds                                                                                                                                                                                                                                                                                                                |
| ------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `trial`, `job`                                                                             | The Harbor trial and job the record came from                                                                                                                                                                                                                                                                        |
| `case`, `system`                                                                           | The case, and the system's `systems.toml` name                                                                                                                                                                                                                                                                       |
| `fingerprint`, `nixos_config_rev`                                                          | The system's fingerprint and the export revision it ran under                                                                                                                                                                                                                                                        |
| `harness_version`                                                                          | The Harbor version whose terminal agent wrote a task's solution                                                                                                                                                                                                                                                      |
| `text`                                                                                     | A case's reply, or on a voted task the git-style diff of the graded `/app` against the task's `environment/app/`, without `.git` or tool caches                                                                                                                                                                      |
| `reward`                                                                                   | A task's reward from its tests                                                                                                                                                                                                                                                                                       |
| `gate_failures`                                                                            | The [gates](../design/families/writing.md#gate-verdicts) a voted output failed, which on a task is `tests` when its reward is below 1                                                                                                                                                                                |
| `error`                                                                                    | The error type of a failed trial. A graded task can retain its diff and gate verdict after an agent error                                                                                                                                                                                                            |
| `finish_reason`                                                                            | The endpoint's finish reason for a single-call reply, or for the completion failure that ended a terminal agent's run, null otherwise                                                                                                                                                                                |
| `stop_details`                                                                             | The `type`, `category`, and `explanation` that the Messages API gave for the refusal that ended the trial, whether a single-call reply or a terminal agent's request, null otherwise                                                                                                                                 |
| `input_tokens`, `cached_tokens`, `cache_write_tokens`, `output_tokens`, `reasoning_tokens` | Token counts summed over the trial's requests, null unless every request reports valid usage. `input_tokens` includes `cached_tokens` and `cache_write_tokens`, and `output_tokens` includes `reasoning_tokens`. An unreported cache count adds 0, while an unreported reasoning count makes `reasoning_tokens` null |
| `cost_usd`                                                                                 | The cost at catalog prices, null when the catalog channel has no price, an input or output token count is unknown, or a cache write has no declared price                                                                                                                                                            |
| `latency_sec`                                                                              | The end-to-end latency, or a terminal agent's execution time                                                                                                                                                                                                                                                         |
| `response_models`, `request_id`                                                            | The distinct models the trial's responses reported, sorted, and the endpoint's request ID for a single-call reply                                                                                                                                                                                                    |

The summary ends with each system's spend from the family's [ledger](#spend-and-budgets).

## Spend and Budgets

```bash
uv run bench spend
uv run bench spend coding-repo --system <name>
```

Every agent attempt, including one that Harbor retries or that a later deletion removes, appends its responses' token counts to its job's `attempts.jsonl`. Collection adds each new attempt to the family's ledger, `spend/<family>.jsonl`, which outlives the jobs. An attempt whose process dies without unwinding leaves no record, so the ledger is then a lower bound.

Each ledger entry keeps the price its system had when collection added it, so a later price change or a system leaving the catalog leaves recorded spend as it was. A response has no known cost when its usage is invalid, when a Messages stream broke before its final usage, or when its price cannot cost it, as when its cache write has no declared price. Each system's spend line imputes those responses from the system's own mean tokens per measured response at the entry's price, with its cache reads and writes counted as plain input when that price cannot cost them. Only a system without measured usage of its own borrows, from the costliest mean of the family's other systems, with their cache reads and writes priced as plain input since another system's caching says nothing about this one's. When no system has measured usage, a response counts at its ceiling, a full context window at the dearest declared input rate plus a full completion allowance. The line names that share for information, and the budget leaves it out until a later run measures usage to impute from. The tokens that projections draw on, and that an unpriced system reports alone, leave out those responses.

Before Harbor starts, `bench run` prints each system's pending trials, the trials that Harbor's diff would rerun, with their projected cost. A system with usage of its own in the ledger uses its mean tokens per trial that received a response, retries included, and any other system borrows the family's costliest tokens per trial at its own prices, again with the lender's cache priced as plain input. When the family sets `budget_usd`, a run with priced pending trials refuses before any job starts if the ledger's known and imputed spend plus the projections that have an estimate exceed it, and one family over its budget refuses the whole run. A run with no priced pending trials, such as one of unpriced systems or one that only regrades, never refuses. A priced system whose pending trials have no usage to estimate from counts no projected dollars, since only a run can give it that usage, and its projection line warns that the run can exceed the budget. Unpriced systems count no dollars. `bench spend` takes the same arguments as `bench run` and prints the ledger and the projection without running anything.

A terminal agent's trajectory `final_metrics` counts only responses with valid usage.

Before adding family trials, a run also sends the [route canaries](../design/systems.md#model-swaps) for systems with pending work. Its printed full-context cost ceiling includes the resolved system prompt and API framing within the context allowance, plus the probe's completion allowance including reasoning. An unknown price or cache-write rate leaves that ceiling unknown. Actual probe spend is recorded separately in `spend/canaries.jsonl` and does not consume family budgets.

### Model Swaps

A fingerprint hashes a system's route, so an endpoint that starts serving another model under an unchanged route keeps the fingerprint. The summary names each system whose pool shows a swap by the signals [Systems](../design/systems.md#model-swaps) defines, with the jobs behind each signal. `bench arena prepare` and `bench close` on a `tests` family refuse such a family, since publishing would start votes on the mixed pool and closing would freeze it.

When the route serves another model, delete the minority job's trials of that system under `jobs/<family>/` and rerun them, or give the system a new name once its route is fixed, as [Authoring](authoring.md#systems) describes.

A canary refusal happens before new family trials start. If the route changed models, remove that system's stale trials and `canaries/<system>.jsonl`, then rerun. For a benign template change, remove only the canary file to establish a new baseline. For a failed probe, correct the reported failure and rerun. Keep `spend/canaries.jsonl` so resetting a baseline preserves past costs.

## Voting

Follow the site's Vote link and choose an available family. [Arena](../design/arena.md#vote-store) describes the production and staging vote stores.

Publishing a family's outputs for voting needs `public_voting = true` in its `family.toml` and a Cloudflare API token with D1 Edit. Set exactly one credential source in `.env`: `BENCH_D1_TOKEN_FILE` names a file holding the token, or `BENCH_D1_ENV_FILE` names an existing dotenv file containing `CLOUDFLARE_API_TOKEN`. The dotenv source reads that token without expanding variables or changing the process environment.

```bash
uv run bench arena prepare writing-zh
uv run bench arena publish staging <publication>.json
uv run bench arena publish production <publication>.json
uv run bench arena status production writing-zh
```

`prepare` writes the publication of the family's current outputs and a Markdown listing beside it, and prints both paths. Review the listing, since publishing that exact file approves it. Publishing again with the same file changes nothing, and an interrupted publish completes when rerun. [Arena](../design/arena.md#publication-and-closing) describes what a publication holds and which cases production votes on.

A family ends for a voter once no comparison is left, and each vote saves as you submit it. [Voting](voting.md) covers eligibility, keys, and issues, and [Arena](../design/arena.md) covers how pairs are chosen.

### Voting Locally

```bash
uv run bench serve
```

Open [localhost:8790/vote/](http://localhost:8790/vote/) to vote on held-out and transcript-derived cases, and on every case of a family without `public_voting`. The command collects current trials, builds the site and publishes only to the local SQLite store. It makes no model requests. Run `bench run` first when those cases lack generated outputs.

Ctrl-C stops the server. Stop it before inspecting or moderating the local store with `bench arena status local`, `bench arena votes local <family>` or the other arena commands. The local store and its browser signing keys persist under `arena/` in the data directory, so a restart preserves votes and cookies.

### Setting Up a Vote Store

`web/wrangler.toml` names each target's database, and `web/migrations/` holds its schema. Create a missing database, record the `database_id` it prints under the target's `[[env.<target>.d1_databases]]` entry, then apply the migrations:

```bash
pnpm --filter web exec wrangler d1 create assistant-bench-arena-staging
pnpm --filter web exec wrangler d1 migrations apply DB --env staging --remote
```

Applying them again after adding one skips the files already applied. The deploy job applies production's migrations before each deploy, so apply them to staging yourself.

The voting Worker reads the three secrets that each target's `secrets.required` in `web/wrangler.toml` lists. Generate each key once per target and pipe it straight into Wrangler, so it never reaches your terminal or shell history, and take the Turnstile secret from the widget whose sitekey the target's `TURNSTILE_SITEKEY` names:

```bash
openssl rand -base64 32 | pnpm --filter web exec wrangler secret put ACTOR_KEY --env staging
openssl rand -base64 32 | pnpm --filter web exec wrangler secret put IP_TAG_KEY --env staging
pnpm --filter web exec wrangler secret put TURNSTILE_SECRET --env staging
```

Replacing `ACTOR_KEY` signs every browser out and reorders every voter's pairs, and replacing `IP_TAG_KEY` starts new network tags, so change them only when a key leaks. To try a branch on staging, build the site and deploy it there:

```bash
pnpm build
pnpm --filter web exec wrangler deploy --env staging
```

### Moderation

Each vote records the browser that cast it, an opaque tag of its network and the time from issuing the pair to recording the vote. `bench arena votes` summarizes a family's votes per network tag, or per browser with `--by actor`, and lists one tag's or browser's votes when given `--ip-tag` or `--actor`:

```bash
uv run bench arena votes production writing-zh
uv run bench arena votes production writing-zh --by actor
uv run bench arena votes production writing-zh --ip-tag <tag>
uv run bench arena exclude production writing-zh --reason <text> --ip-tag <tag>
uv run bench arena restore production writing-zh --vote <id> --vote <id>
```

A summary row shows the votes, the browsers behind a tag or the tags a browser used, the share of each choice, the median time per vote, the first and last vote, and how many votes are excluded. A listing shows each vote's systems, choice and time, and the reason for an exclusion. Votes whose tag has been cleared show `(cleared)` in place of a tag. Copy tags and browser IDs from these listings, since only the voting Worker can compute a tag.

`exclude` and `restore` take exactly one of `--ip-tag`, `--actor` or `--vote`, which repeats, and print how many votes changed. [Arena](../design/arena.md#moderation) describes what an exclusion covers and which signals warrant a closer look.

## Closing a Round

```bash
uv run bench close writing-zh
uv run bench close writing-zh --snapshot <snapshot-path>
```

For a voted family, stop `bench serve` before closing. `bench close` saves the available production and local votes that can no longer be undone under `votes/<family>/` in the data directory, combines them, and writes `reports/<family>/<round>.json`, dated by the UTC day. Families without `public_voting` read only the local store. `--snapshot` recloses a saved file without reading either store. For a `tests` family, `bench close <family>` collects its trials and aggregates rewards. Commit the report through a PR. [Reports](../design/reports.md) describes its contents.

## The Data Directory

Everything a run produces stays out of Git in the [data directory](../conventions/data.md#the-data-directory):

```text
assistant-bench/
├── agents/<harness>/<version>/<platform>/  # Verified vendor CLI executables for installed agents
├── arena/                                 # Local vote database, signing keys and serve lock
├── canaries/<system>.jsonl                 # Every route probe, including its identities, protocol, usage and outcome
├── canaries/jobs/<job>/                    # Probe Harbor jobs, separate from family collection
├── canaries/task/                          # The fixed synthetic probe task
├── cases/<family>.toml                     # Local cases, appended to the committed ones
├── cases/<family>/<task>/                  # Local tasks, beside the committed ones
├── checks/<family>/<job>/                  # Oracle and nop jobs from `bench check`
├── jobs/<family>/<job>/                    # Harbor job directories, each with its config.json, attempts.jsonl, and one directory per trial
├── outputs/<family>.jsonl                  # Collected Harbor trial records
├── publications/<family>/<id>.json         # Publications from `bench arena prepare`, each with its .md listing
├── spend/<family>.jsonl                    # The family's ledger of agent attempts
├── spend/canaries.jsonl                    # Probe attempts outside family budgets
├── tasks/<family>/<case>/                  # Harbor tasks rendered from the cases
└── votes/<family>/<taken-at>.json          # Vote cuts that `bench close` saved
```

Run `harbor view` on a family's job directory to browse its trials.
