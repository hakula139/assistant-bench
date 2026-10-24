# Systems

A system is one generation configuration under test: a model route, completion and context allowances, an effort, a harness, and an optional system prompt. [`systems.toml`](../../systems.toml) declares each system as a table, whose keys are covered in [Authoring](../guide/authoring.md#systems).

## Resolution

A system specifies either a catalog model with `model` or a bare `route`. A catalog model supplies its vendor, single route, prices, configured completion and context allowances, default [display name](../guide/authoring.md#systems), and effort rules. The system's `effort` must be one the model accepts, and a model requiring effort takes its catalog default when the system sets none. A bare route supplies its own capacities and display name. Both a channel priced at zero and a route without a `price` report as unpriced.

The catalog's `maxTokens` is the configured completion allowance, including reasoning. It is not proof of the endpoint's maximum capacity. The writing baseline explicitly selects the strongest supported effort, named by each table's effort suffix. This is a deliberate configuration and does not establish that higher effort improves every answer. Terminus uses the configured context window minus the completion allowance as its input limit, reserving completion space through Harbor's native context management.

`prompt` resolves to text before anything hashes or sends it, where `nixos-config:<client>` selects a rendered instruction file from the export.

## Fingerprints

A system's fingerprint hashes its resolved route, capacities, effort, harness, generation protocol, and the SHA-256 of its prompt text. The table name and public labels are excluded. A task family binds its harness before fingerprinting, so a system has a distinct identity on case and task families.

Votes and scores pool only within one fingerprint. Altering a system's generation settings requires a new table name, preserving the old name's frozen results. A nixos-config bump that leaves the resolved generation settings unchanged keeps its pool.

On every PR and push to `main`, the [fingerprint check](../conventions/ci.md#checks) resolves each table at the base and head, each against its own nixos-config export, and fails when a table keeps its name but not its fingerprint, which stops a dependency bump from changing a system under its old name.

A local `bench run` refuses each selected system whose earlier trials recorded a fingerprint other than its fingerprint under the family's harness, naming both and pointing at [Authoring](../guide/authoring.md#systems), so the same drift fails before Harbor starts. Every output also records the nixos-config rev it ran under.

Closed reports freeze each public label and output fingerprint in `Report.systems`, including unranked entrants. A retired system can carry its latest prior identity into a new report only when known fingerprints match. Unknown provenance remains null and cannot prove retired continuity. Display-name changes affect only later reports. [Reports](reports.md#frozen-identity) defines these checks.

## Harnesses

`System.harness` names an entry in `harnesses.HARNESSES`, a frozen `Harness` that pairs a Harbor agent's import path with how the bench configures it. Its `options` builder consumes the resolved system, and the job sends the system's route after its `model_prefix` as the model name.

| Harness      | Agent                                         | Model name       | Environment                                      |
| ------------ | --------------------------------------------- | ---------------- | ------------------------------------------------ |
| `generate`   | `assistant_bench.harnesses.generate:Generate` | `<route>`        | `assistant_bench.harnesses.host:HostEnvironment` |
| `terminus-2` | `assistant_bench.harnesses.terminus:Terminus` | `openai/<route>` | Harbor's Docker environment                      |

Harbor gives every trial of a job the same environment. A job whose harnesses are all `single_call` runs in `HostEnvironment`, which starts no container and refuses every command and file transfer, and any other job runs in Harbor's Docker environment. `bench check`'s oracle and nop jobs run in Docker, since both act in the task's container.

`systems.toml` has no `harness` key, so a case family runs every system as `generate`, the single-call agent, while a task family runs every system it lists under the family's `harness`, which must be an agent that acts in the task's environment, so `terminus-2` is the one a task family takes today. A harness that cannot send a system's prompt, which `terminus-2` cannot, makes `bench check` and `bench run` refuse the family while it lists a system with one. `bench check` builds Harbor's `oracle` and `nop` agents directly, so only the system harnesses live in the registry.

`terminus-2` is Harbor's terminal agent: it sends its JSON protocol to the model from the host and drives the container's shell through tmux, which its task images install. Its generation version combines the bench's generation protocol with the installed Harbor version, since Harbor supplies its prompt and loop. `generate` uses only the bench's generation protocol. Each native agent reports that same version, so a Harbor upgrade changes Terminus fingerprints while unrelated grading or tooling changes preserve the single-call protocol.

The `openai/` prefix pins LiteLLM to its OpenAI-compatible provider, and `harnesses/endpoint.py` builds the chat-completions client from the endpoint variables, which `Terminus` passes to LiteLLM as it constructs it. The key file, the CA bundle, the extra headers, and the local address therefore behave as they do for `Generate`, while the key never enters a job's `kwargs`, `env`, or `config.json`.

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

Systems sharing a route share one Harbor `concurrency_group`, which caps that route at four trials at a time. A case family's job runs at most eight trials at once and a task family's job four, since each task trial can hold an agent and a verifier container. A rendered case has a 900-second operational timeout, and authored tasks set `[agent] timeout_sec`. Terminus uses its native completion loop without a family turn cutoff. Harbor retries a failed trial once.
