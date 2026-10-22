# AGENTS.md: assistant-bench

assistant-bench is a public benchmark in which every AI assistant system receives the same instruction and the owner compares outputs blind and in pairs. [Harbor](https://github.com/harbor-framework/harbor) runs every family, while the `bench` command renders cases, runs Harbor jobs, and collects their trials.

This file specifies the rules for every change and points to the conventions for each area. `README.md` covers the project for users, while [`docs/`](docs/) contains the guides, conventions, design, and research.

```bash
uv run bench check smoke                    # Validate a family, running Harbor's oracle on a task family
uv run bench run smoke-text                 # Generate a family's missing trials for every system it lists
uv run bench ingest smoke-text              # Collect a family's trials into outputs/<family>.jsonl
uv run bench serve                          # Serve the vote page for every family with outputs
uv run bench close prose-zh                 # Write a family's report to reports/<family>/<round>.json
```

## Project Layout

The tree lists only the top-level entries whose names leave their purpose unclear.

```text
.
├── .env.example                            # Endpoint variables that `bench` loads from an ignored .env
├── .worktreeinclude                        # Gitignored agent memory that Claude Code copies into new worktrees
├── docs/                                   # Guides, conventions, design, and research
├── families/                               # One directory per family and leaderboard, described in docs/guide/authoring.md
├── flake.nix                               # Dev shells, pre-commit hooks, checks, and the nixos-config export
├── reports/                                # Closed rounds, written only by `bench close`
├── src/assistant_bench/                    # The `bench` package, mapped in docs/design/architecture.md
├── systems.toml                            # Systems under test, one table each
├── tests/                                  # pytest suite mirroring the package, with fixtures under tests/fixtures/
└── web/                                    # The results site and the local vote page, a pnpm workspace package
```

## Read Before You Edit

| When the change touches                             | Read                                                                                                       |
| --------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| Python code                                         | [Python](docs/conventions/python.md) and the [module map](docs/design/architecture.md#module-map)          |
| The site under `web/`                               | [TypeScript](docs/conventions/typescript.md) and [Site](docs/design/site.md)                               |
| Tests                                               | [Testing](docs/conventions/testing.md)                                                                     |
| A case, a family, or `systems.toml`                 | [Authoring](docs/guide/authoring.md) and the family's page, such as [Prose](docs/design/families/prose.md) |
| Cases, outputs, fixtures, or log lines              | [Data](docs/conventions/data.md)                                                                           |
| Fingerprints, harnesses, or the nixos-config export | [Systems](docs/design/systems.md)                                                                          |
| The scheduler, the vote store, or the vote API      | [Arena](docs/design/arena.md)                                                                              |
| Ranking or the report schema                        | [Ranking](docs/design/ranking.md) and [Reports](docs/design/reports.md)                                    |
| PRs or issues                                       | [GitHub](docs/conventions/github.md)                                                                       |
| The dev shell, hooks, toolchain, or config files    | [Development](docs/conventions/development.md)                                                             |
| A workflow, a failing check, or the deploy          | [CI](docs/conventions/ci.md)                                                                               |

## Rules for Every Change

- Run pre-commit hooks on every commit, and never bypass them with `--no-verify` or `PRE_COMMIT_ALLOW_NO_CONFIG`. In each new checkout or worktree, run `nix develop -c true`, `pnpm install`, and `uv sync` once to provide the generated config, the Node tools, and the virtualenv.
- Run the [verification](#verification) block before review.
- Add new words to `.cspell/words.txt` in alphabetical order, as `cspell` scans Python sources too.
- Commit only synthetic or public cases. As [Data](docs/conventions/data.md) describes, transcript-derived and held-out cases, their outputs, and Harbor jobs live in the data directory, and a test fails if a committed case file holds a private source.
- Keep internal hostnames, the endpoint's operator and product names, its specific headers, registry mirrors, tokens, and local paths out of the repository, issues, and PRs. Such headers go in `BENCH_API_HEADERS`, and committed text calls the endpoint an OpenAI-compatible endpoint configured in `.env`.
- Keep the lockfiles on public registries, as a test fails if `uv.lock` or `pnpm-lock.yaml` names a host other than PyPI or npm.
- Name a key file by path in `.env`, and never put a key value in `.env`, a fixture, a log line, or a Harbor job config, since Harbor writes each agent's `kwargs` and `env` into the job's `config.json`.
- In docs, issues, and PRs, name systems by their `systems.toml` table names, which are public model names plus a suffix for a variant. Routes and channel IDs stay in `systems.toml` and the data directory.
- Only `bench close` writes under `reports/`, and a report holds aggregates under public system names alone.
- Never run paid model calls in CI, as tests reach models only through the stub endpoint in `tests/support.py`.
- Keep commands minimal and settings in config files: `bench` reads `systems.toml` and `family.toml` while taking at most a family name and, for `bench run`, the listed systems to limit it to, and environment variables hold only secrets and per-environment values, as [Development](docs/conventions/development.md#where-settings-live) lists.
- In committed files, state each convention on its own terms and never name the repositories that conventions, layout, or design were drawn from. nixos-config appears only where the bench uses it: the flake input, its export, and `nixos-config:<client>` prompts. Research notes and design pages may cite the external benchmarks, papers, and tools whose methods the bench uses.

## Verification

Run after implementation and before review:

```bash
uv run ruff check                           # Lint
uv run ruff format --check                  # Check formatting
uv run mypy                                 # Type check (strict)
uv run pytest                               # Run the tests that need no Docker, with coverage
uv run pytest -m docker                     # Run the Harbor trials under Docker, which need no secrets
pnpm format                                 # Check formatting of the site, JSON, Markdown, and YAML
pnpm lint                                   # Lint Markdown and the site
pnpm spellcheck                             # Spell check
pnpm typecheck                              # Type check the site
pnpm test                                   # Test the site: schema, charts, pages, and the vote page
pnpm build                                  # Build the site from reports/
nix flake check                             # After a flake change, run the hooks in the Nix sandbox
```

[CI](docs/conventions/ci.md#checks) lists which job runs each check.

## Code Review

Beyond the general review, check in particular:

- Layering: imports follow the [import layers](docs/design/architecture.md#import-layers).
- Data hygiene: nothing private reaches a committed file, a fixture, or a log line.
- Blinding: nothing the vote page receives identifies a system, including timing and error text, as [Arena](docs/design/arena.md#blinding) describes.
- Scorer coverage: a new scorer covers ingest, close, its report variant, and its board on the site.
- Reruns: a change to rendered tasks, agent configs, or `systems.toml` keeps Harbor's reuse and the fingerprint rule intact, as [Architecture](docs/design/architecture.md#reruns) and [Systems](docs/design/systems.md#fingerprints) describe.
