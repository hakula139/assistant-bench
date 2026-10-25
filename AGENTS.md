# AGENTS.md: assistant-bench

assistant-bench publishes leaderboards for AI models and agents, ranked through public blind pairwise voting or automated tests. Every system in a family receives the same instruction. [Harbor](https://github.com/harbor-framework/harbor) runs every family, while the `bench` command renders cases, runs Harbor jobs, and collects their trials. Real votes from every device use one production D1 database. Staging uses a separate D1 database for validation.

This file specifies the rules for every change and points to the conventions for each area. `README.md` covers the project for users, while [`docs/`](docs/) contains the guides, conventions, design, and research.

```bash
uv run bench check                               # Validate every family and its reference solutions
uv run bench run --system <name>                  # Generate a system's missing trials
uv run bench arena status production writing-zh  # Inspect the published voting family
uv run bench close writing-zh --snapshot <path>   # Close a voted round from canonical evidence
```

## Project Layout

The tree lists only the top-level entries whose names leave their purpose unclear.

```text
.
├── .env.example                            # Endpoint variables that `bench` loads from an ignored .env
├── .worktreeinclude                        # Gitignored agent memory that Claude Code copies into new worktrees
├── families/                               # One directory per family and leaderboard
├── flake.nix                               # Dev shells, pre-commit hooks, checks, and the nixos-config export
├── reports/                                # Closed rounds, written only by `bench close`
├── src/assistant_bench/                    # The `bench` package, mapped in docs/design/architecture.md
├── src/assistant_bench_arena/              # Voting contracts, ledger, and native Worker services
├── systems.toml                            # Systems under test, one table each
└── web/                                    # The results site and public vote page, a pnpm workspace package
```

## Read Before You Edit

| When the change touches                             | Read                                                                                                           |
| --------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| Python code                                         | [Python](docs/conventions/python.md) and the [module map](docs/design/architecture.md#module-map)              |
| The site under `web/`                               | [TypeScript](docs/conventions/typescript.md) and [Site](docs/design/site.md)                                   |
| Tests or fixtures                                   | [Testing](docs/conventions/testing.md)                                                                         |
| A case, a family, or `systems.toml`                 | [Authoring](docs/guide/authoring.md) and the family's page, such as [Writing](docs/design/families/writing.md) |
| Case sources, outputs, or the data directory        | [Data](docs/conventions/data.md)                                                                               |
| Fingerprints, harnesses, or the nixos-config export | [Systems](docs/design/systems.md)                                                                              |
| The scheduler, the vote store, or the vote API      | [Arena](docs/design/arena.md)                                                                                  |
| Ranking or the report schema                        | [Ranking](docs/design/ranking.md) and [Reports](docs/design/reports.md)                                        |
| PRs or issues                                       | [GitHub](docs/conventions/github.md)                                                                           |
| The dev shell, hooks, toolchain, or config files    | [Development](docs/conventions/development.md)                                                                 |
| A workflow, a failing check, or the deploy          | [CI](docs/conventions/ci.md)                                                                                   |

## Rules for Every Change

- Run pre-commit hooks on every commit, and never bypass them with `--no-verify` or `PRE_COMMIT_ALLOW_NO_CONFIG`. In each new checkout or worktree, run `nix develop -c true`, `pnpm install`, and `uv sync` once to provide the generated config, the Node tools, and the virtualenv.
- Run the [verification](#verification) block before review.
- Add new words to `.cspell/words.txt`, as `cspell` scans Python sources too.
- Commit only synthetic or public cases. Keep transcript-derived and held-out cases, all outputs, and Harbor jobs in the data directory, as [Data](docs/conventions/data.md) describes.
- Keep internal hostnames, the endpoint's operator and product names, its specific headers, registry mirrors, tokens, and local paths out of committed files, fixtures, log lines, issues, and PRs. Such headers go in `BENCH_API_HEADERS`, and committed text calls the endpoint an OpenAI-compatible endpoint configured in `.env`.
- Keep lockfiles and requirements files throughout the repository, including `families/`, on public registries, with `pyproject.toml` naming PyPI and `.npmrc` naming npm.
- Name a key file by path in `.env`, and never put a key value in `.env`, a fixture, a log line, or a Harbor job config. Harbor writes each agent's `kwargs` and `env` into the job's `config.json`, so a secret reaches an agent only through a file one of those values names.
- In docs, issues, and PRs, name systems by their `systems.toml` table names, which are public model names plus a suffix naming a variant's prompt or effort, or the model's snapshot. Routes and channel IDs stay in `systems.toml`, the nixos-config export, and the data directory.
- Only `bench close` writes under `reports/`, and a report holds aggregates under public system names alone.
- Never run paid model calls in CI, as tests reach models only through the stub endpoint in `tests/support.py`.
- Keep commands minimal and settings in config files: `bench` takes only the arguments that pick what a command acts on and reads every setting from `systems.toml` and `family.toml`, while environment variables hold only secrets and per-environment values, as [Development](docs/conventions/development.md#where-settings-live) lists.
- In committed files, state each convention on its own terms and never name the repositories that conventions, layout, or design were drawn from. nixos-config appears only where the bench uses it: the flake input, its export, the shared Renovate preset, and `nixos-config:<client>` prompts. Research notes and design pages may cite the external benchmarks, papers, and tools whose methods the bench uses.

## Verification

Run after implementation and before review:

```bash
uv run ruff check                                     # Lint
uv run ruff format --check                            # Check formatting
uv run mypy                                           # Type check (strict)
uv run basedpyright                                   # Check the shared editor type policy
uv run basedpyright --project families/coding-repo    # Check authored task scripts on Python 3.13
uv run pytest                                         # Run the tests that need no Docker, with coverage
uv run pytest -m docker                               # Run the Harbor trials under Docker, which need no secrets
pnpm format                                           # Check formatting of the site, JSON, Markdown, and YAML
pnpm lint                                             # Lint Markdown and the site
pnpm spellcheck                                       # Spell check
pnpm typecheck                                        # Type check the site
pnpm test                                             # Test the site, with coverage: schema, charts, pages, and the vote page
pnpm build                                            # Build the site from reports/
nix flake check                                       # After a flake change, run the hooks in the Nix sandbox
```

[CI](docs/conventions/ci.md#checks) lists which job runs each check.

## Code Review

Beyond the general review, check in particular:

- Layering: imports follow the [import layers](docs/design/architecture.md#import-layers).
- Data hygiene: nothing private reaches a committed file, a fixture, or a log line.
- Blinding: nothing the vote page receives identifies a system, including timing and error text, as [Arena](docs/design/arena.md#blinding) describes.
- Scorer coverage: a new scorer covers collection, closing, its report variant, and its board on the site.
- Reruns: a change to rendered tasks, agent configs, a job's environment, or `systems.toml` keeps Harbor's reuse and the fingerprint rule intact, as [Architecture](docs/design/architecture.md#reruns) and [Systems](docs/design/systems.md#fingerprints) describe.
