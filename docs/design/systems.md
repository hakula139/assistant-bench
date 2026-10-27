# Systems

A system is one generation configuration under test: a model route and API, completion and context allowances, an effort, a harness, and an optional system prompt. [`systems.toml`](../../systems.toml) declares each system as a table, whose keys are covered in [Authoring](../guide/authoring.md#systems).

## Resolution

A system specifies either a catalog model with `model` or a bare `route`. A catalog model supplies its vendor, single route, prices, configured completion and context allowances, default [display name](../guide/authoring.md#systems), and effort rules. The system's `effort` must be one the model accepts, and a model requiring effort takes its catalog default when the system sets none. A bare route supplies its own capacities and display name. Both a channel priced at zero and a route without a `price` report as unpriced.

The catalog's `maxTokens` is the configured completion allowance, including reasoning. It is not proof of the endpoint's maximum capacity. The benchmark baseline explicitly selects high effort or its supported equivalent for every model. This is a deliberate configuration and does not establish that higher effort improves every answer. Terminus uses the configured context window minus the completion allowance as its input limit, reserving completion space through Harbor's native context management.

`api` selects `openai-chat-completions` by default, `openai-responses`, or `anthropic-messages`. The catalog declares supported APIs in `apis` and supported efforts in `thinking.efforts`. An unsupported API, explicit effort, or required default is rejected during resolution. Opus uses native Messages with the catalog's adaptive thinking mode, while GPT systems use Responses. Requests never substitute an effort or transport after a failure.

`prompt` resolves to text before anything hashes or sends it, where `nixos-config:<client>` selects a rendered instruction file from the export.

## Fingerprints

A system's fingerprint hashes its harness, model name, generation version, and every kwarg the harness gives its agent except the trial labels, so a trial's agent config changes only with the system's fingerprint or name. Those kwargs carry the resolved API, effort, and completion allowance, the prompt under `generate`, the context window as the input limit under `terminus-2`, and the thinking mode of native Messages. The generation version adds the declared generation protocol, the locked Harbor version under `terminus-2`, and the native protocol and API version of native Messages. SDK versions, the table name, and public labels are excluded. A task family binds its harness before fingerprinting, so a system has a distinct identity on case and task families.

Votes and scores pool only within one fingerprint. A nixos-config bump that leaves the resolved generation settings unchanged keeps its pool.

A system name stands for exactly one fingerprint in every closed report of its family. Once a closed report under `reports/` names a system, any change to its fingerprint in that family needs a new table name, even one caused only by the fingerprint code, and the old name keeps its frozen results. Before its first closed report, a system keeps its name through a fingerprint change, while its stale trials are deleted and it reruns.

`fingerprints.json` maps each runnable `<family>/<system>` to its fingerprint, and a test fails when it is stale. On every PR and push to `main`, the [fingerprint check](../conventions/ci.md#checks) compares the base revision's committed manifest with the fingerprints the head resolves, so each side comes from its own revision's code. It fails for each changed system that a closed report of its family names and lists the others, which rerun. A base without a manifest has nothing to compare, which happens only for the change that introduced it.

A local `bench run` refuses each selected system whose earlier trials recorded a fingerprint other than its fingerprint under the family's harness, naming both, so the same drift fails before Harbor starts. It asks for a new name when a closed report of the family names the system, and otherwise for deleting the stale trials. Every output also records the nixos-config rev it ran under.

Closed reports freeze each public label and output fingerprint in `Report.systems`, including unranked entrants. A retired system can carry its latest prior identity into a new report only when known fingerprints match. Unknown provenance remains null and cannot prove retired continuity. Display-name changes affect only later reports. [Reports](reports.md#frozen-identity) defines these checks.

## Harnesses

`System.harness` names an entry in `harnesses.HARNESSES`, a frozen `Harness` that pairs a Harbor agent's import path with how the bench configures it. Its `options` builder consumes the resolved system, and the job sends the system's route after its `model_prefix` as the model name.

| Harness      | Agent                                         | Model name       | Environment                                      |
| ------------ | --------------------------------------------- | ---------------- | ------------------------------------------------ |
| `generate`   | `assistant_bench.harnesses.generate:Generate` | `<route>`        | `assistant_bench.harnesses.host:HostEnvironment` |
| `terminus-2` | `assistant_bench.harnesses.terminus:Terminus` | `openai/<route>` | Harbor's Docker environment                      |

Harbor gives every trial of a job the same environment. A job whose harnesses are all `single_call` runs in `HostEnvironment`, which starts no container and refuses every command and file transfer, and any other job runs in Harbor's Docker environment. `bench check`'s oracle and nop jobs run in Docker, since both act in the task's container.

`systems.toml` has no `harness` key, so a case family runs every system as `generate`, the single-call agent, while a task family runs every system it lists under the family's `harness`, which must be an agent that acts in the task's environment, so `terminus-2` is the one a task family takes today. A harness that cannot send a system's prompt, which `terminus-2` cannot, makes `bench check` and `bench run` refuse the family while it lists a system with one. `bench check` builds Harbor's `oracle` and `nop` agents directly, so only the system harnesses live in the registry.

`terminus-2` is Harbor's terminal agent: it sends its JSON protocol to the model from the host and drives the container's shell through tmux, which its task images install. Its generation version combines the bench's declared generation protocol with the locked Harbor version, since Harbor supplies its prompt and loop. `generate` uses only the bench's generation protocol. Resolved systems capture those versions, and each native agent reports the same version after checking that the installed Harbor matches the lock. A Harbor upgrade changes Terminus fingerprints while unrelated grading or tooling changes preserve the single-call protocol.

The `openai/` prefix pins LiteLLM's Chat Completions path to its OpenAI-compatible provider. Responses and Messages use their native SDKs through Harbor's `BaseLLM` interface. `harnesses/endpoint.py` builds clients for both routes with the same CA bundle, the extra headers from `BENCH_API_HEADERS`, and the same local address. OpenAI uses `BENCH_API_BASE_URL`, while Messages requires its independently configured `BENCH_ANTHROPIC_BASE_URL`, the root to which the SDK appends `/v1/messages`. The SDK would also add the headers in `ANTHROPIC_CUSTOM_HEADERS`, which an assistant's shell may export for its own gateway, so `bench` removes that variable before Harbor starts, and native Messages refuses it when set. Each client authenticates with the configured key through its SDK, and automatic redirects are disabled. Each agent closes its transport after the trial. The key never enters a job's `kwargs`, `env`, or `config.json`.

The resolved API selects the transport for either harness. Responses calls use `store=False` and retain native output text, status, request ID, and usage. Messages streams typed SDK events and requires `message_stop`, with explicit API version `2023-06-01`. Only ordered text blocks enter its answer, while thinking blocks and signatures stay in private native artifacts and conversation state. A writing response that exhausts its allowance or refuses remains an incomplete attempt. Terminus propagates failed, refused, or filtered responses without retrying for quality, while Harbor retains its native context and output-length handling. Collection prices reported tokens from the shared catalog.

Native Messages input totals include ordinary input, cache reads, and cache creation. Collection prices cache reads and writes separately. Cost remains unknown when nonzero writes lack an applicable declared price, including one-hour cache creation. The clients add no cache breakpoints. Reported thinking counts remain separate from inclusive output tokens, and missing counts remain unknown.

Both harnesses validate reported token counters through one accounting boundary before recording totals. Negative or fractional counts, inconsistent cache or thinking subsets, and missing required counts retain private diagnostic evidence while tokens and cost remain unknown. Explicitly reported zero usage remains zero. Terminus aggregates usage from completed responses only, so a failed or cancelled request leaves earlier and later usage intact, while an included completed response without valid usage makes the aggregate unknown.

Terminus attaches complete SDK output items or native content blocks to each private assistant message and replays the currently selected conversation, including reasoning metadata. Context slices and summarized handoffs retain the items owned by their messages. Metadata stays outside model-visible user text and public outputs. Harbor owns the terminal command loop, conversation accounting, and summarization.

`bench` loads the first `.env` in the working directory or a parent, then sets `LITELLM_MODE=PRODUCTION`, which Harbor inherits, so LiteLLM never loads a `.env` of its own.

Harbor records each trial's agent and environment import paths in the job's `config.json`, and `harbor job resume` imports them from there, so an import path must never change once a job uses it.

## The nixos-config Export

The flake takes [nixos-config](https://github.com/hakula139/nixos-config) as a `flake = false` input and imports only pure Nix files from it, which evaluate without Nixpkgs or a host. Its `nixos-config` package writes one JSON file, which the dev shell exports as `BENCH_NIXOS_CONFIG`:

| Field          | Content                                                                                               |
| -------------- | ----------------------------------------------------------------------------------------------------- |
| `rev`          | The input's commit, recorded on every output                                                          |
| `catalog`      | The model catalog: vendors, channels and routes, prices, effort rules, and display names              |
| `instructions` | Each client's rendered global instruction file, keyed as `claudeCode`, `codex`, `omp`, and `opencode` |

## Concurrency

Systems sharing a route share one Harbor `concurrency_group`, which caps that route at four trials at a time. A case family's job runs at most eight trials at once and a task family's job four, since each task trial can hold an agent and a verifier container.

A rendered case has a one-hour operational watchdog, while authored tasks keep their `[agent] timeout_sec`. Harbor cancels the agent at that deadline. HTTP read timeouts are disabled, while connection, write, and pool timeouts remain finite. Terminus uses its native completion loop without a family turn cutoff. Harbor retries eligible failures once and excludes agent timeouts.
