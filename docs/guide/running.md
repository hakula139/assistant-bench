# Running

Learn how to point the bench at a model endpoint, check and run a family, collect its outputs, vote on them, and close a round, with the setup covered in [Quickstart](quickstart.md).

## The Model Endpoint

Every system calls its configured API at the endpoint in `.env`, which `bench` loads from the working directory or a parent: OpenAI-compatible Chat Completions or Responses, or the Anthropic Messages API for `api = "anthropic-messages"`. Copy [`.env.example`](../../.env.example) and fill in:

| Variable                        | Value                                                                                                                                                                  |
| ------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `BENCH_API_BASE_URL`            | The OpenAI-compatible base URL, including `/v1`                                                                                                                        |
| `BENCH_ANTHROPIC_BASE_URL`      | The Messages API base URL, to which the SDK appends `/v1/messages`. Required for a Messages system                                                                     |
| `BENCH_API_KEY_FILE`            | A file holding the API key, so the key is never copied into the checkout                                                                                               |
| `BENCH_API_CA_FILE`             | Optional. A CA bundle for an endpoint whose certificate the system store does not trust                                                                                |
| `BENCH_API_HEADERS`             | Optional. A JSON object of ordinary extra request headers                                                                                                              |
| `BENCH_API_SECRET_HEADERS_FILE` | Optional. A file holding a JSON object of confidential extra request headers                                                                                           |
| `BENCH_API_LOCAL_ADDRESS`       | Optional. A local IP address to bind outgoing connections to, such as `0.0.0.0` to keep them on IPv4. A bound client connects directly and ignores the proxy variables |

Both APIs share the key file, CA bundle, headers, and local address.

Put confidential header values in the file named by `BENCH_API_SECRET_HEADERS_FILE`. The bench audits artifacts and receipt metadata against those values and the authentication headers actually sent. `BENCH_API_HEADERS` cannot set `Authorization`, `Proxy-Authorization` or `x-api-key`. Header names must be unique ignoring case within and across both maps.

Without `BENCH_API_LOCAL_ADDRESS`, the client honors `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, and `NO_PROXY`.

Each system's route is the model ID the endpoint expects. Requests use the system's resolved completion allowance, including reasoning, and its selected effort through the configured API. A case trial sends the instruction as the user message and the optional system prompt. A task trial uses the family's harness to drive the task's container.

## Checking a Family

```bash
uv run bench check
uv run bench check smoke-text
uv run bench check coding-repo --task <task>
```

`bench check` validates each named family, or every family when none is named, and `--task` limits one task family to the named tasks. It validates `family.toml` and its systems. For a `cases.toml` family, it validates the cases and renders them into Harbor tasks, which needs neither Docker nor an endpoint. For a `tasks/` family, it also runs each task's reference solution and a do-nothing agent, as [Authoring](authoring.md#tasks) describes, which needs Docker but no endpoint.

Checks use committed sources by default and need no cloud credentials. Add `--private` to include cases from the pinned private repository, which requires GitHub authentication but no D1 access.

## Running a Family

```bash
uv run bench run
uv run bench run smoke-text
uv run bench run --system <name>
```

`bench run` renders each case into a Harbor task, or checks each task of a task family as `bench check` does without running it, writes a job with one agent for each system listed in `family.toml`, and runs it through the Harbor CLI.

A run without a family sends every family that lists systems, naming families limits it to those, and `--system` alone sends the named systems to every family that lists them, failing only when no family lists one.

Each case and system pair runs `samples` times with automatic retries disabled. A reply cut off by the completion allowance remains a recorded incomplete output and fails the completion gate. An HTTP error fails the trial. Case trials start no container and need no Docker.

On a task family, the harness runs until completion or the task's operational timeout. The task's tests then grade `/app` in a fresh container, which needs Docker. Harbor reruns an errored trial on the next run. Until a rerun succeeds, a trial whose agent timed out or failed keeps its tests' verdict, while one the tests never graded has none.

Installed agents require a named local Docker context using a Unix socket or Docker Desktop. Select it with `DOCKER_CONTEXT`. A standalone `DOCKER_HOST` and contexts using SSH or TCP endpoints are unsupported, since the model proxy runs on the client host. This is checked before CLI downloads or model calls.

Runs require a clean implementation revision retained by canonical GitHub `main`, as [Data](../conventions/data.md#the-data-directory) explains, an authenticated `BENCH_DATA_REPOSITORY`, and the private D1 [credential source](#voting). The command pins the implementation and private case revisions before execution. Tasks and containers run locally in a disposable command workspace.

When Harbor exits, `bench run` stores completed trial artifacts in the private repository, seals the family's collected output generation in private D1 and prints the summary that [Collecting Outputs](#collecting-outputs) describes, also when Harbor fails.

Because a rerun generates only the trials that no earlier job of the family finished, adding a system to `family.toml` and rerunning sends requests for that system alone.

An edited task reruns, regrades, or reuses its trials by its version bump, as [Authoring](authoring.md#tasks) lists, and an edited case [reruns](../design/architecture.md#reruns) on every system. Publication proves the selected source and output identities. Earlier releases and saved evidence retain their frozen content.

Deleting a local workspace does not reset completed trials. Reruns restore their immutable artifacts from the private repository.

Removing a system from the family's `systems` stops new trials and current collection. Closed reports keep their frozen identities, and a removed `reference` needs a replacement for later rounds.

A run refuses a system whose earlier trials or closed reports recorded another fingerprint, as [Systems](../design/systems.md#fingerprints) describes, and [Authoring](authoring.md#systems) gives the fix.

Add a system's table and list it in each family's `systems`, or add cases to `cases.toml`. Have new or edited cases [reviewed](authoring.md), merge the source changes, then run `uv run bench run --system <name>` or `uv run bench run`.

## Collecting Outputs

```bash
uv run bench collect
uv run bench collect smoke-text
```

`bench run` collects its own trials, and closing a `tests` family re-collects its trials. `bench collect` re-collects on demand for every family with at least one job, or for the named families, and prints the summary. Voted reports read the measurements from these records.

It restores the family's trial artifacts and seals a new output generation in private D1, keeping at most `samples` records per case, system, and fingerprint. Each generation pins its cases, collection revision and public system labels. Which trial fills each slot follows [Architecture](../design/architecture.md#reruns).

Collection names errors, gate failures and unsolved tasks, which can reveal a wrong specification or a task too hard to rank by. For vendor-authored tasks, compare each system's pass rate on its own vendor's tasks with its rate on the others. The summary ends with [ledger spend](#spend-and-budgets).

A graded task keeps its diff and verdict even after an agent error. Measurements remain unknown unless every required request reports valid usage, while a reported zero stays zero. Input includes cache reads and writes, and output includes reasoning. An unreported cache count adds zero, while unreported reasoning leaves its total unknown. [Reports](../design/reports.md#results) defines the aggregate denominators and missing-cost labels.

## Spend and Budgets

```bash
uv run bench spend
uv run bench spend coding-repo --system <name>
```

Private D1 records request admissions before dispatch and receipts after responses. Collection reconciles receipts with the family's ledger, which outlives local jobs. An interrupted request can leave its final usage unknown and block another paid attempt until inspected.

### Recorded Spend

Each ledger entry keeps the price frozen before execution, so a later price change or a system leaving the catalog leaves recorded spend as it was. A response has no known cost when its usage is invalid, when a Messages stream broke before its final usage, or when its price cannot cost it, as when its cache write has no declared price. A terminal agent's trajectory `final_metrics` counts only responses with valid usage.

### Missing-Usage Estimates

Each system's spend line imputes unknown costs from its own mean tokens per measured response at the entry's price, with cache reads and writes counted as plain input when that price cannot cost them. A system without measured usage of its own borrows the costliest mean of the family's other systems, with their cache reads and writes priced as plain input since another system's caching says nothing about this one's.

When no system has measured usage, a response counts at its ceiling, a full context window at the dearest declared input rate plus a full completion allowance. The line names that share for information, and the budget leaves it out until a later run measures usage to impute from. The tokens that projections draw on, and that an unpriced system reports alone, leave out those responses.

### Budget Admission

Before Harbor starts, `bench run` prints each system's pending trials, the trials that Harbor's diff would rerun, with their projected cost. A system with usage of its own in the ledger uses its mean tokens per trial that received a response, retries included, and any other system borrows the family's costliest tokens per trial at its own prices, again with the lender's cache priced as plain input. When the family sets `budget_usd`, a run with priced pending trials refuses before any job starts if the ledger's known and imputed spend plus the projections that have an estimate exceed it, and one family over its budget refuses the whole run. A run with no priced pending trials, such as one of unpriced systems or one that only regrades, never refuses. A priced system whose pending trials have no usage to estimate from counts no projected dollars, since only a run can give it that usage, and its projection line warns that the run can exceed the budget. Unpriced systems count no dollars. `bench spend` takes the same arguments as `bench run` and prints the ledger and the projection without running anything.

Before adding family trials, a run also sends the [route canaries](../design/systems.md#model-swaps) for systems with pending work. Its printed full-context cost ceiling includes the resolved system prompt and API framing within the context allowance, plus the probe's completion allowance including reasoning. An unknown price or cache-write rate leaves that ceiling unknown. Private D1 records probe spend separately from family budgets.

### Model Swaps

A fingerprint hashes a system's route, so an endpoint that starts serving another model under an unchanged route keeps the fingerprint. The summary names each system whose pool shows a swap by the signals [Systems](../design/systems.md#model-swaps) defines, with the jobs behind each signal. `bench arena prepare` and `bench close` on a `tests` family refuse such a family, since publishing would start votes on the mixed pool and closing would freeze it.

When the route serves another model, give the system a new name once its route is fixed, as [Authoring](authoring.md#systems) describes.

A canary refusal happens before new family trials start. For a failed probe, correct the reported failure and rerun. After verifying that a route change is benign, run `uv run bench reset-canary <system>` to clear that system's baseline contexts. The command reports how many it cleared, requires only private D1 access and preserves probe records, request receipts and spend. A changed model needs a new system name. Canary baselines and past probe costs remain in private D1.

## Voting

Follow the site's Vote link and choose an available family. [Arena](../design/arena.md#vote-store) describes the production, staging and private vote stores.

Publishing a family's outputs for production voting needs `public_voting = true` in its `family.toml`. Cloud commands need a Cloudflare API token with D1 Edit. Set exactly one credential source in `.env`: `BENCH_D1_TOKEN_FILE` names a file holding the token, or `BENCH_D1_ENV_FILE` names an existing dotenv file containing `CLOUDFLARE_API_TOKEN`. The dotenv source reads that token without expanding variables or changing the process environment.

```bash
uv run bench arena prepare writing-zh production
uv run bench arena inspect <publication-id>
uv run bench arena publish staging <publication-id>
uv run bench arena publish production <publication-id>
uv run bench arena status production writing-zh
```

`prepare` pins an existing sealed output generation and saves the publication in private D1, returning its ID. Run `bench collect` first when outputs are missing or stale. Review `inspect` before publishing that ID. Publishing again with the same ID changes nothing, and an interrupted publish completes when rerun. [Arena](../design/arena.md#publication-and-closing) describes what a publication holds and which cases production votes on.

A family ends for a voter once no comparison is left, and each vote saves as you submit it. [Voting](voting.md) covers eligibility, keys, and issues, and [Arena](../design/arena.md) covers how pairs are chosen.

### Private Voting

```bash
uv run bench arena prepare writing-zh private
uv run bench arena inspect <publication-id>
uv run bench arena publish private <publication-id>
```

Open the Access-protected private vote page to vote on held-out and transcript-derived cases, and on families without `public_voting`. Preparing and publishing make no model requests. Run `bench run` and `bench collect` first when those cases lack collected outputs.

Use `bench arena status private` and `bench arena votes private <family>` to inspect this store. Votes persist in its D1 database independently of the client checkout.

### Setting Up a Vote Store

`web/wrangler.toml` names each target's database, and `web/migrations/` holds its schema. Create a missing database, record the `database_id` it prints under the target's `[[env.<target>.d1_databases]]` entry, then apply the migrations:

```bash
pnpm --filter web exec wrangler d1 create assistant-bench-arena-staging
pnpm --filter web exec wrangler d1 migrations apply DB --env staging --remote
```

Applying them again after adding one skips the files already applied. The deploy job applies production's migrations before each deploy, so apply them to staging yourself.

The voting Worker reads the secrets that each target's `secrets.required` in `web/wrangler.toml` lists. Generate each key once per target and pipe it straight into Wrangler, so it never reaches your terminal or shell history, and take the Turnstile secret from the widget whose sitekey the target's `TURNSTILE_SITEKEY` names. The private target also needs its configured Access identity secrets.

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
uv run bench close writing-zh --snapshot <snapshot-id>
```

For a voted family, `bench close` seals the available production and private votes that can no longer be undone in private D1, combines them, and writes `reports/<family>/<round>.json`. Families without `public_voting` read only the private store. `--snapshot` replays a sealed snapshot by ID, preserving its close time, category order and each store's pinned outputs without reading the current family, catalog or private repository. For a `tests` or `checkers` family, closing uses the collected generation's pinned cases, code revision and public system labels. Commit the report through a PR. [Reports](../design/reports.md) describes its contents.
