# CI

Learn what each GitHub Actions workflow runs, how the site deploys, and how workflow steps are written.

## Checks

CI runs on pushes to `main`, pull requests, and manual dispatch:

| Job             | Checks                                                                                                                             |
| --------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| Nix Flake Check | `nix flake check`, which runs the pre-commit hooks without the Node and Ruff ones                                                  |
| Node Check      | `pnpm format`, `pnpm lint`, `pnpm spellcheck`, `pnpm typecheck`, and `pnpm test`                                                   |
| Build Site      | `pnpm build`, from the reusable `build.yml` that the deploy workflows also call                                                    |
| Python Check    | `uv sync --locked`, `ruff check`, `ruff format --check`, mypy, pytest with coverage uploaded to Codecov, and the fingerprint check |
| Harbor Smoke    | `uv run bench check smoke` and `uv run pytest -m docker` under Docker                                                              |
| CI Result       | Fails when any job above fails or is cancelled, so branch protection can require this job alone                                    |

The fingerprint check runs `uv run bench fingerprints <base>` against a PR's base commit, and on a push to `main` against the previous head, which flags two PRs that pass alone but conflict once both land. It resolves every `systems.toml` table at both commits, each against its own nixos-config export, and fails when a table keeps its name but its fingerprint changes, so a changed system takes a new name and the old one keeps its results.

No job sends a paid request, and no CI job reads any secret other than the Cachix and Codecov tokens. List each new job in CI Result's `needs`.

## Deploy

The site deploys to Cloudflare Workers static assets, which `web/wrangler.toml` configures, and each deploy workflow publishes the `web/dist` artifact that its own `build.yml` run uploads:

- **Deploy** runs on each push to `main` and deploys to `bench.hakula.xyz` in the `production` environment, which only `main` may deploy to.
- **Preview Deploy** runs `wrangler preview` for each PR in the `preview` environment, creating a Worker Preview named `pr-<number>` under the same Worker, and comments its URLs on the PR. The Preview's `workers.dev` URL serves the PR's latest build with `X-Robots-Tag: noindex`, and each push also gets a URL of its own.
- **Preview Cleanup** deletes the Preview once the PR closes, marks the PR's deployments inactive, and updates the comment to say so. It runs on `pull_request_target`, which also fires for a PR with merge conflicts and runs `main`'s copy of the workflow, so changes to it take effect only once merged.

All three read the repository's `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` secrets, so a missing secret fails the run. Previews skip PRs from forks and Dependabot, which receive no secrets. The site has no bindings or variables, so the `previews` block stays empty.

## Workflow Steps

Write multiline step text as one heredoc, never as repeated `printf` or `echo >>` calls. When the text includes a command's output, read the output into a variable first, since a failed command substitution inside a heredoc does not fail the step under `bash -e`.
