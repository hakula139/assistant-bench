# Data

## Data Classes

Each case or task declares its `source`, which determines where it and everything generated from it may live:

| Source       | Meaning                                                        | Lives in                                   |
| ------------ | -------------------------------------------------------------- | ------------------------------------------ |
| `synthetic`  | Written for the bench                                          | `families/<family>/cases.toml`             |
| `public`     | Taken from a public source whose license allows redistribution | `families/<family>/cases.toml`             |
| `transcript` | Derived from the owner's assistant transcripts                 | The data directory's `cases/<family>.toml` |
| `held-out`   | Kept private so a leaderboard cannot be fit to it              | The data directory's `cases/<family>.toml` |

A task in a task family declares its source the same way, in its `task.toml`'s `[metadata]`. A committed task is `synthetic` or `public` and lives in `families/<family>/tasks/<task>/`, while a `transcript` or `held-out` task lives under the data directory's `cases/<family>/<task>/`.

A `transcript` case or task passes gitleaks before it enters the local cases, since every system receives it. `nix shell nixpkgs#gitleaks -c gitleaks dir <path>` exits nonzero when it finds a secret.

## The Data Directory

Run artifacts live in the data directory, which `BENCH_DATA_DIR` names and which defaults to `$XDG_DATA_HOME/assistant-bench/`, or `~/.local/share/assistant-bench/`, and code reaches it only through `paths.data_dir()`. Rendered tasks, Harbor jobs and collected outputs stay there regardless of their cases' source, since outputs of committed cases still carry routes, request IDs and endpoint metadata. [Running](../guide/running.md#the-data-directory) lists its layout.

`bench check` refuses and names each visible entry under `cases/` that no family reads, so a run cannot silently drop its cases. Hidden files and directories are ignored, so a backup can stay under `cases/.<name>/`.

`bench arena publish` refuses a transcript-derived or held-out case before anything leaves the machine, as [Arena](../design/arena.md#publication-and-closing) describes. A publication sends the reviewed cases, each passing output's content, and for every output its system, fingerprint, verdict, length and content digest, along with the family's display labels, defects and reference. The vote cuts that `bench close` saves under `votes/` omit network tags and output content.

Point a single run at a scratch data directory with `BENCH_DATA_DIR=<dir>`. Leave `XDG_DATA_HOME` alone, since uv keeps its managed interpreters under it and would install a new CPython in `<dir>` and relink `.venv` to it, which breaks once `<dir>` is removed.
