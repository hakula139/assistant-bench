# Development

## Dev Shell

The dev shell provides uv, Node.js, and pnpm, and exports `BENCH_NIXOS_CONFIG`, described [below](#the-nixos-config-input). Follow [Quickstart](../guide/quickstart.md#setup) to set up a checkout and its test browser.

The root Python project contains the `assistant_bench` CLI, and `uv sync` installs its dependencies with the `dev` group. The voting Worker is TypeScript in `web/worker/`, which `web/wrangler.toml` configures and the `web` package's Wrangler deploys.

Beside the production, staging and private [vote stores](../design/arena.md#vote-store), the disposable `local` environment serves the Worker under `wrangler dev` with Cloudflare's always-passing Turnstile test keys.

The project supports Python 3.14, selected by `.python-version` and bounded by the root and typing package requirements. Ruff infers its target from the root requirement, while mypy and basedpyright use the selected interpreter. The host and authored task code share one strict typing policy in the root `pyproject.toml`, which permits missing library typing metadata while missing imports and unknown or incompatible API types remain errors. Each coding task's hidden tests and reference resolve imports against its reference package, while its starter resolves only its own modules. Editors use the root `.venv`, where `uv sync` installs the checkers CI runs.

## Pre-Commit Hooks

`flake.nix` enables devkit's [pre-commit hooks](https://github.com/hakula139/devkit/blob/v3/docs/hooks.md). [`AGENTS.md`](../../AGENTS.md#verification) lists the checks outside the hooks. mypy and Prettier, with its Astro and Tailwind plugins, run from the lockfiles.

The authored task applications, reference solutions and hidden tests follow the host code's formatting and spelling too. Deliberately wrong variants are unformatted, untyped patches, and the root verifier test requires each one to fail on an assertion.

## Where Settings Live

Commands stay minimal, and settings live in config files:

| Setting                                                                          | Lives in                                                                                                              |
| -------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| Ruff, mypy, pytest (including coverage and the default `-m "not docker"`), uv    | `pyproject.toml`'s `[tool.*]` tables                                                                                  |
| Systems under test                                                               | `systems.toml`                                                                                                        |
| The generation and Messages protocol versions and the Messages API version       | `pyproject.toml`'s `[tool.assistant-bench]`                                                                           |
| A family's scorer, systems, and samples                                          | `families/<family>/family.toml`                                                                                       |
| Model completion and context allowances                                          | The shared model catalog                                                                                              |
| Harbor job settings                                                              | `jobs.py`, which builds each job config at run time                                                                   |
| Rendered task settings                                                           | `tasks.py`                                                                                                            |
| Admission, cap, and retention limits of the vote API                             | `web/worker/limits.ts`, with the per-minute request limit in `web/wrangler.toml`                                      |
| The Cloudflare account, the voting Worker's environments, and their D1 databases | `web/wrangler.toml`                                                                                                   |
| Point-fit parameters, the actor cap, and diagnostic coverage rules               | `scoring/ranking.py`, `scoring/weighting.py`, and `report.py`, and recorded in each report                            |
| The model endpoint                                                               | The ignored `.env`, with API keys and confidential headers in the files it names                                      |
| Prettier, markdownlint, cspell, and lychee                                       | Their own files at the repository root; Prettier replaces devkit's shared style, and markdownlint overrides its rules |
| The site's Astro, TypeScript, ESLint, shadcn/ui, and Vitest settings             | `web/astro.config.ts`, `web/tsconfig.json`, `web/eslint.config.js`, `web/components.json`, and `web/vitest.config.ts` |
| Codecov's project and patch status targets, per path                             | `codecov.yml`                                                                                                         |

Environment variables carry secrets and per-environment values: the `BENCH_API_*` endpoint settings, native `BENCH_ANTHROPIC_BASE_URL`, the D1 [credential source](../guide/running.md#voting), `BENCH_DATA_REPOSITORY` naming the private `owner/repository` source, the Nix store path `BENCH_NIXOS_CONFIG` from the dev shell, an optional `BENCH_DATA_DIR` that moves the [vendor CLI cache](data.md#the-data-directory), and GitLab's `CI_PAGES_URL` and `CI_PROJECT_URL` for [Pages builds](ci.md#deploy). The voting Worker's origins and D1 bindings belong to its environments in `web/wrangler.toml`, and its keys are Worker secrets, which [Running](../guide/running.md#setting-up-a-vote-store) sets.

Job configs are generated per run and never committed. Harbor copies each one into its job directory as `config.json`.

## Toolchain and Pins

- The flake pins the stable Nixpkgs channel for Node.js and the hooks, and devkit supplies uv. uv installs the CPython that `.python-version` names from its python-build-standalone builds, and `python-preference = "only-managed"` in `[tool.uv]` keeps it from using any other Python on `PATH`. A host's first install takes the newest patch release the pinned uv knows, while an existing install stays on its patch until `uv python upgrade`.
- On NixOS, that interpreter is a generic Linux executable, so it runs through `programs.nix-ld`, which also provides the C++ runtime and zlib that Linux wheels link.
- Renovate automerges lock file maintenance, including the flake lock update that moves the nixos-config and devkit inputs. When such a bump moves a fingerprint, the stale `fingerprints.json` fails its test, so the PR waits until someone rewrites the manifest and applies the rule in [Systems](../design/systems.md#fingerprints). Likewise, when it moves Nixpkgs' pnpm, the `pages-pnpm` flake check fails, and the PR waits until someone sets `BENCH_PNPM_VERSION` in `.gitlab/ci/pages.yml` to the new version.
- `uv.lock` pins the Python dependencies and `pnpm-lock.yaml` pins the Node tools, with Renovate managing both.

## The nixos-config Input

The flake reads the model catalog and the rendered assistant instructions from [nixos-config](https://github.com/hakula139/nixos-config), as [Systems](../design/systems.md#the-nixos-config-export) describes:

```bash
nix build .#nixos-config                    # Build the export on its own
nix flake update nixos-config               # Move to the latest nixos-config commit
```

A bump that changes a system's resolved prompt, route, or effort changes its fingerprint, and [Systems](../design/systems.md#fingerprints) states whether the system keeps its name. The rendered instructions cite devkit's source path, so a devkit bump changes the prompts of the `nixos-config:<client>` systems too.
