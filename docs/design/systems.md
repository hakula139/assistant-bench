# Systems

A system is one configuration under test: a model route, an effort, a harness, and an optional system prompt. [`systems.toml`](../../systems.toml) declares each system as a table, whose keys are covered in [Authoring](../guide/authoring.md#systems).

## Resolution

A system specifies either a catalog model with `model` or a bare `route`. A catalog model supplies its route from its single channel, its prices, and its effort rules: the system's `effort` must be one the model accepts, and a model requiring effort takes its catalog default when the system sets none. Both a channel priced at zero and a route without a `price` report as unpriced.

`prompt` resolves to text before anything hashes or sends it, where `nixos-config:<client>` selects a rendered instruction file from the export. A catalog model with more than one channel fails to resolve, so such a system sets its `route` and `price` directly.

## Fingerprints

A system's fingerprint hashes its resolved route, effort, harness, and the SHA-256 of its prompt text. Because the table name is excluded, renaming a table preserves the fingerprint, whereas modifying what the system sends changes it.

Votes and scores pool only within one fingerprint. Altering a system's route, effort, or prompt therefore requires a new table name, preserving the old name's frozen results. Bumping the nixos-config input changes a fingerprint only when it alters a system's resolved route, effort, or prompt, so a bump that leaves a system untouched keeps its pool. Every output also records the nixos-config rev it ran under.

## Harnesses

`System.harness` names an entry in `harnesses.HARNESSES`, which maps it to a Harbor agent: either a bench agent by import path or an agent shipped with Harbor by name. Every system defaults to `generate`, the single-call agent, while `bench check` uses Harbor's `oracle`.

Harbor records each trial's agent import path in the job's `config.json`, and `harbor job resume` imports it from there, ensuring an import path never changes once a job uses it.

## The nixos-config Export

The flake takes [nixos-config](https://github.com/hakula139/nixos-config) as a `flake = false` input and imports only pure Nix files from it, which evaluate without Nixpkgs or a host. Its `nixos-config` package writes one JSON file, which the dev shell exports as `BENCH_NIXOS_CONFIG`:

| Field          | Content                                                                                               |
| -------------- | ----------------------------------------------------------------------------------------------------- |
| `rev`          | The input's commit, recorded on every output                                                          |
| `catalog`      | The model catalog: channels and routes, prices, and effort rules                                      |
| `instructions` | Each client's rendered global instruction file, keyed as `claudeCode`, `codex`, `omp`, and `opencode` |
| `proseTics`    | The prose tic list that the instruction files share                                                   |

## Concurrency

Systems sharing a route share one Harbor `concurrency_group`, which caps that route at four trials at a time, while a job runs at most eight trials at once. Each trial is bounded by the family's `max_output_tokens`, its `samples`, and a 900-second agent timeout, and a failed trial is retried once.
