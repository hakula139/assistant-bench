# Data

## Data Classes

Each case or task declares its `source`, which determines where it and everything generated from it may live:

| Source       | Meaning                                                        | Lives in                                       |
| ------------ | -------------------------------------------------------------- | ---------------------------------------------- |
| `synthetic`  | Written for the bench                                          | `families/<family>/cases.toml`                 |
| `public`     | Taken from a public source whose license allows redistribution | `families/<family>/cases.toml`                 |
| `transcript` | Derived from the owner's assistant transcripts                 | The private repository's `cases/<family>.toml` |
| `held-out`   | Kept private so a leaderboard cannot be fit to it              | The private repository's `cases/<family>.toml` |

A task in a task family declares its source the same way, in its `task.toml`'s `[metadata]`. A committed task is `synthetic` or `public` and lives in `families/<family>/tasks/<task>/`, while a `transcript` or `held-out` task lives under the private repository's `cases/<family>/<task>/`.

A `transcript` case or task passes gitleaks before it enters the private repository, since every system receives it. `nix shell nixpkgs#gitleaks -c gitleaks dir <path>` exits nonzero when it finds a secret.

## The Data Directory

The private repository holds private case sources and immutable Harbor trial artifacts. `BENCH_DATA_REPOSITORY` names its `owner/repository`. GitHub CLI authenticates repository metadata, while native Git needs SSH access to the same repository. Verify both with `gh auth status` and `ssh -T git@github.com` before running cloud commands. Runs pin the private source revision before loading cases. Outputs of committed cases also remain private, since they carry routes, request IDs and endpoint metadata.

Private D1 holds runs, request admissions and receipts, the spend ledger, collected output generations, canary records, prepared publications and sealed vote snapshots. Prices and source revisions are frozen with their records. Historical records retain unknown provenance when the original revision is unavailable.

Rendered tasks, restored artifacts and Harbor jobs exist in a temporary workspace for the command's lifetime. Docker execution stays on the client host. Removing local directories preserves completed cloud records, but deleting an active workspace interrupts execution and can lose unfinished output.

New runs and collections require a clean implementation revision retained by the canonical GitHub `main` history. The command restores that revision's public inputs and child implementation into its temporary workspace. A local or mirror-only commit must reach canonical `main` before it can create persistent runtime records.

`bench check` validates committed sources without cloud authentication. `bench check --private` also authenticates the configured private repository, pins its `main` revision and validates the combined sources. It refuses each visible entry under the private `cases/` tree that no family reads. Hidden files and directories are ignored. The pinned private files exist only for the command's lifetime.

Production publications exclude transcript-derived and held-out cases. The Access-protected private arena accepts them, as [Arena](../design/arena.md#publication-and-closing) describes. Prepared publications pin cases, outputs and public system labels. Sealed vote snapshots pin each store's output generation, category order, closing revision and close time, so replay needs no current family or catalog.

`BENCH_DATA_DIR` names only the disposable vendor CLI cache. It defaults to `$XDG_DATA_HOME/assistant-bench/`, or `~/.local/share/assistant-bench/`, and code reaches it through `paths.data_dir()`. Downloads are verified before use. Deleting this directory requires downloading those tools again.

Canary records and accounting live in private D1, apart from family trials and budgets. Resetting a baseline preserves recorded probe costs.
