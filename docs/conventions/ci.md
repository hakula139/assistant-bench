# CI

Learn what each GitHub Actions workflow runs, how the site deploys, and how workflow steps are written.

## Checks

CI runs on pushes to `main`, pull requests, and manual dispatch:

| Job             | Checks                                                                                                                                                                      |
| --------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Nix Flake Check | `nix flake check`, which runs the pre-commit hooks without the Node and Ruff ones                                                                                           |
| Node Check      | `pnpm format`, `pnpm lint`, `pnpm spellcheck`, `pnpm typecheck`, and `pnpm test` with coverage uploaded to Codecov                                                          |
| Build Site      | `pnpm build`, uploading `web/dist` as the `site-dist` artifact that the deploy jobs publish                                                                                 |
| Python Check    | `uv sync --locked`, `ruff check`, `ruff format --check`, mypy, pytest with coverage uploaded to Codecov, and the fingerprint check                                          |
| Harbor Smoke    | `uv run pytest -m docker` under Docker, including the oracle and nop check that `bench check smoke` runs, then `bench check coding-repo` on the coding tasks selected below |
| CI Result       | Fails when any job above fails or is cancelled                                                                                                                              |

The fingerprint check enforces the rule in [Systems](../design/systems.md#fingerprints) by running `uv run bench fingerprints <base>` against a PR's base commit, and on a push to `main` against the previous head, which flags two PRs that pass alone but conflict once both land. Python Check checks out the full history with `fetch-depth: 0`, since the check builds the base commit's nixos-config export.

On a PR, Harbor Smoke checks each current coding task whose files differ from the PR's base, passing its ID through `bench check coding-repo --task <id>`. Added and renamed tasks are checked, while deleted tasks need no trial. Changes to the Python package, `systems.toml`, the coding family's `family.toml`, Python dependencies or version, Nix inputs or shell, or Harbor Smoke's workflow, setup action, or selector check every coding task. Other changes skip the coding-family trials. Pushes to `main` and manual runs check every coding task.

The `main` ruleset requires CI Result and both Codecov patch statuses: `codecov/patch` for Python and `codecov/patch/web` for the site. Python Check and Node Check upload their coverage under the `python` and `web` flags, and `codecov.yml` gives each flag its own project and patch status.

Both jobs authenticate the upload with a GitHub OIDC token, so they alone hold `id-token: write`, and the action falls back to a tokenless upload for PRs from forks, which receive no OIDC token. A failed upload fails the job, so a PR stuck on a missing patch check shows the cause in CI.

Only the deploy jobs below read secrets, and each reads them in its deploy step alone. List each new check job in CI Result's `needs`.

## Deploy

The site deploys to Cloudflare Workers static assets, which `web/wrangler.toml` configures. Two jobs in the CI workflow publish the `site-dist` artifact that Build Site uploads, so each push builds the site once, and a separate workflow removes previews:

- **Deploy Site:** runs on each push to `main` and on manual dispatch from `main` once CI Result passes, so a failing check on `main` blocks the deploy. It deploys to `bench.hakula.xyz` in the `production` environment, which only `main` may deploy to. Deploys wait their turn in the `production` concurrency group, and each skips a commit that `main` has moved past, so a slow run or a rerun of an older commit never overwrites a newer deploy.

- **Deploy Preview:** runs `wrangler preview` in the `preview` environment as soon as Build Site passes on each PR, without waiting for the other checks, creating a Worker Preview named `pr-<number>` under the same Worker, and comments its URLs on the PR.

  The Preview's `workers.dev` URL serves the PR's latest build with `X-Robots-Tag: noindex`, and each push also gets a URL of its own.

  It joins the concurrency group `preview-<number>`, which it shares with Preview Cleanup, only once Build Site passes, so closing a PR cancels a deploy already in the group, while a deploy that starts after the close, including one that waits for a running cleanup, finds the PR closed and skips.

- **Preview Cleanup:** deletes the Preview once the PR closes, marks the PR's deployments inactive, and updates the comment to say so. It runs on `pull_request_target`, which also fires for a PR with merge conflicts and runs `main`'s copy of the workflow, so changes to it take effect only once merged.

  Because that event carries the deploy secrets, the job checks out only `main` and never runs the PR's code.

Deploy Site, Deploy Preview, and Preview Cleanup read the repository's `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` secrets, so a missing secret fails the run. Previews skip PRs from forks and Dependabot, which receive no secrets. The site has no bindings or variables, so the `previews` block stays empty.

## Workflow Steps

Each job that needs the dev shell sets `defaults.run.shell` to run bash inside `nix develop`, or `nix develop .#python` for the Python jobs, so its `run:` steps hold bare commands.

actionlint runs shellcheck only on steps whose shell is plain `bash`, so every multiline script sets `shell: bash` and prefixes the commands that need the dev shell with `nix develop -c`.

Write multiline step text as one heredoc, never as repeated `printf` or `echo >>` calls.

When the text includes a command's output, read the output into a variable first, since a failed command substitution inside a heredoc does not fail the step under `bash -e`.
