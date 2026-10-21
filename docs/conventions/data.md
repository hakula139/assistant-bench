# Data

What the bench stores, where each kind of data lives, and what may be committed or published.

## Data Classes

Each case declares its `source`, which determines where the case and everything generated from it may live:

| Source       | Meaning                                                        | Lives in                                    |
| ------------ | -------------------------------------------------------------- | ------------------------------------------- |
| `synthetic`  | Written for the bench                                          | `families/<family>/cases.jsonl`             |
| `public`     | Taken from a public source whose license allows redistribution | `families/<family>/cases.jsonl`             |
| `transcript` | Derived from the owner's assistant transcripts                 | The data directory's `cases/<family>.jsonl` |
| `held-out`   | Kept private so a leaderboard cannot be fit to it              | The data directory's `cases/<family>.jsonl` |

`test_repository.py` fails when a committed case file holds a `transcript` or `held-out` case.

## The Data Directory

Everything a run produces lives under `$XDG_DATA_HOME/assistant-bench/`, which defaults to `~/.local/share/assistant-bench/`, and code reaches it only through `paths.data_dir()`. Rendered tasks, Harbor jobs, and ingested outputs stay there regardless of their cases' source, since outputs of committed cases still carry routes, request IDs, and the endpoint's replies. [Running](../guide/running.md#the-data-directory) lists its layout.

## Committed and Published Text

- Commit only synthetic or public cases, along with fixtures built from generic names such as `acme/large` and `example.com`.
- Keep the endpoint's hostname, its operator's name, headers specific to it, internal registry mirrors, tokens, and local paths out of committed files, fixtures, log lines, issues, and PRs. Committed text calls it an OpenAI-compatible endpoint configured in `.env`.
- `pyproject.toml` and `.npmrc` name the public registries, and `test_repository.py` fails when `uv.lock` or `pnpm-lock.yaml` names any other host.

## Public Names

Docs, issues, and PRs name a system by its `systems.toml` table name, which is a public model name plus, for a variant, a suffix naming its prompt or effort. Routes and channel IDs stay in `systems.toml`, the nixos-config export, and the data directory.

## Secrets

`.env` names the API key and CA bundle by path, so no key value enters `.env`, a fixture, a log line, or a Harbor job config. Because Harbor writes each agent's `kwargs` and `env` into the job's `config.json`, a secret reaches an agent only through a file that one of those values names.
