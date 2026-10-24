# Systems

A system is one configuration under test: a model route, an effort, a harness, and an optional system prompt. [`systems.toml`](../../systems.toml) declares each system as a table, whose keys are covered in [Authoring](../guide/authoring.md#systems).

## Resolution

A system specifies either a catalog model with `model` or a bare `route`. A catalog model supplies its vendor, its route from its single channel, its prices, the default [display name](../guide/authoring.md#systems), and its effort rules: the system's `effort` must be one the model accepts, and a model requiring effort takes its catalog default when the system sets none. A catalog model without exactly one channel fails to resolve, so such a system sets its `route`, `vendor`, and `price` directly. Both a channel priced at zero and a route without a `price` report as unpriced.

`prompt` resolves to text before anything hashes or sends it, where `nixos-config:<client>` selects a rendered instruction file from the export.

## Fingerprints

A system's fingerprint hashes its resolved route, effort, harness, and the SHA-256 of its prompt text. Because the table name and the display name are excluded, renaming either preserves the fingerprint, whereas modifying what the system sends changes it. A task family runs each system under its own `harness`, so one table carries one fingerprint on the case families, under `generate`, and another on a task family, while every fingerprint the case families record stays as it was.

Votes and scores pool only within one fingerprint. Altering a system's route, effort, or prompt therefore requires a new table name, preserving the old name's frozen results. Bumping the nixos-config input changes a fingerprint only when it alters a system's resolved route, effort, or prompt, so a bump that leaves a system untouched keeps its pool.

On every PR and push to `main`, the [fingerprint check](../conventions/ci.md#checks) resolves each table at the base and head, each against its own nixos-config export, and fails when a table keeps its name but not its fingerprint, which stops a dependency bump from changing a system under its old name.

A local `bench run` refuses each selected system whose earlier trials recorded a fingerprint other than its fingerprint under the family's harness, naming both and pointing at [Authoring](../guide/authoring.md#systems), so the same drift fails before Harbor starts. Every output also records the nixos-config rev it ran under.

## Harnesses

`System.harness` names an entry in `harnesses.HARNESSES`, a frozen `Harness` that pairs a Harbor agent's import path with how the bench configures it. Its `options` builder returns the agent's kwargs from a system and the family's settings, and the job sends the system's route after its `model_prefix` as the model name.

| Harness      | Agent                                         | Model name       | Environment                                      |
| ------------ | --------------------------------------------- | ---------------- | ------------------------------------------------ |
| `generate`   | `assistant_bench.harnesses.generate:Generate` | `<route>`        | `assistant_bench.harnesses.host:HostEnvironment` |
| `terminus-2` | `assistant_bench.harnesses.terminus:Terminus` | `openai/<route>` | Harbor's Docker environment                      |

Harbor gives every trial of a job the same environment. A job whose harnesses are all `single_call` runs in `HostEnvironment`, which starts no container and refuses every command and file transfer, and any other job runs in Harbor's Docker environment. `bench check`'s oracle and nop jobs run in Docker, since both act in the task's container.

`systems.toml` has no `harness` key, so a case family runs every system as `generate`, the single-call agent, while a task family runs every system it lists under the family's `harness`, which must be an agent that acts in the task's environment, so `terminus-2` is the one a task family takes today. A harness that cannot send a system's prompt, which `terminus-2` cannot, makes `bench check` and `bench run` refuse the family while it lists a system with one. `bench check` builds Harbor's `oracle` and `nop` agents directly, so only the system harnesses live in the registry.

`terminus-2` is Harbor's terminal agent: it sends its JSON protocol to the model from the host and drives the container's shell through tmux, which its task images install. Its prompt and loop ship inside Harbor, so a Harbor upgrade can change them under the same agent config, and each output records the Harbor version that ran it as `harness_version`, which the collection summary reports when a family's outputs span several.

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

Systems sharing a route share one Harbor `concurrency_group`, which caps that route at four trials at a time, while a case family's job runs at most eight trials at once and a task family's job four, since each task trial can run an agent container and a verifier container. Each request is bounded by the family's `max_output_tokens`, a rendered case's trial by a 900-second agent timeout, and a task's trial by its `[agent] timeout_sec` and the family's `max_turns`, and a failed trial is retried once.
