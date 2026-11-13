# CI

## Checks

GitHub Actions runs on pushes to `main`, pull requests, and manual dispatch:

| Job                   | Checks                                                                                                                                                                       |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| nix / Nix (Linux)     | The shared Nix workflow runs `nix flake check`, which holds the GitLab Pages pin checks, and `nix develop -c run-hooks`.                                                     |
| python / Python Check | The shared Python workflow runs pytest and uploads its coverage to Codecov.                                                                                                  |
| Bench Check           | basedpyright and the fingerprint check.                                                                                                                                      |
| Web Check             | `pnpm lint`, `pnpm typecheck`, and `pnpm test` with coverage uploaded to Codecov, then `pnpm build` and a dry-run voting Worker bundle, uploading `web/dist` as `site-dist`. |
| Harbor Smoke          | `uv run pytest -m docker` under Docker, including the oracle and nop check that `bench check smoke` runs, then `bench check coding-repo` on the coding tasks selected below  |
| CI Result             | Fails when any job above fails or is cancelled                                                                                                                               |

The fingerprint check runs `uv run bench fingerprints`, which enforces the rule in [Systems](../design/systems.md#fingerprints) against the fingerprints that the closed reports under `reports/` froze. It reads only the checked-out tree, so the push run on `main` catches a fingerprint change and a close that pass alone but conflict once both land.

Harbor Smoke selects changed coding tasks against the PR base or the previous head on a push to `main`, passing each ID through `bench check coding-repo --task <id>`. Added and renamed tasks are checked, while deleted tasks need no trial. Changes under `src/`, to the coding family's shared files, `systems.toml`, the coding family's `family.toml`, Python dependencies or version, Nix inputs or shell, or Harbor Smoke's workflow check every coding task. Other changes skip the coding-family trials. Manual runs have no comparison base and check every coding task. The core Docker smoke tests run for every event.

The `main` ruleset requires CI Result and both Codecov patch statuses: `codecov/patch` for Python and `codecov/patch/web` for the site. `codecov.yml` scopes each status to `src/` or `web/` and waits for both uploads, which authenticate with a GitHub OIDC token, so only the two uploading jobs hold `id-token: write`.

Only the deploy jobs below read repository secrets, and only in the steps that run Wrangler. Harbor Smoke uses the automatic read-only GitHub token to fetch pinned Codex release metadata. List each new check job in CI Result's `needs`.

Set every job's `timeout-minutes` to several times its measured successful duration, including the full coding-family run for Harbor Smoke. Revisit the budget when the workload changes so a stalled job releases its runner and concurrency group promptly.

Web Check installs Playwright's Chromium headless shell without `--with-deps`, since the runner image already carries its system libraries and the apt downloads stalled the job past its timeout.

## Deploy

Production applies the pending D1 migrations of `web/migrations/` and then deploys the voting Worker with the built site as its assets, both through Wrangler in `web/`. `web/wrangler.toml` configures each environment's Worker, database, rate limit and cron.

Static PR previews use the asset-only `assistant-bench-preview` Worker in `web/wrangler.toml`, with no production routes or voting bindings. Both deploy jobs consume the `site-dist` artifact, and a separate workflow removes previews:

- **Deploy Voting:** runs on each push to `main` and on manual dispatch from `main` once CI Result passes. It deploys in the `production` environment, which only `main` may deploy to. The `production` concurrency group retains up to 100 pending deploys without cancelling a running deploy. Each queued deploy skips a commit that `main` has moved past, so older checks finishing later preserve the current head's queued deployment.

- **Deploy Preview:** runs `wrangler preview` in the `preview` environment once Web Check passes on each PR, creating a Worker Preview named `pr-<number>` under the static preview Worker, and comments its URLs on the PR.

  The Preview's `workers.dev` URL serves the PR's latest build with `X-Robots-Tag: noindex`, and each push also gets a URL of its own.

  It joins the concurrency group `preview-<number>`, which it shares with Preview Cleanup, only once Web Check passes, so closing a PR cancels a deploy already in the group, while a deploy that starts after the close finds the PR closed and skips.

- **Preview Cleanup:** deletes the Preview once the PR closes, marks the PR's deployments inactive, and updates the comment to say so. It runs on `pull_request_target`, which also fires for a PR with merge conflicts and runs `main`'s copy of the workflow, so changes to it take effect only once merged.

  Because that event carries the deploy secrets, the job checks out only `main` and never runs the PR's code.

Deploy Voting, Deploy Preview and Preview Cleanup read the repository's `CLOUDFLARE_API_TOKEN` secret, while the account comes from `web/wrangler.toml`. That token needs D1 Edit as well as Workers Scripts Edit, because Deploy Voting applies migrations before every deploy, and `wrangler d1 migrations apply` creates its `d1_migrations` table if missing, a write, even when no migration is pending. A deploy keeps the Worker secrets that [Running](../guide/running.md#setting-up-a-vote-store) sets once per environment. Static previews have no voting bindings and skip PRs from forks and Dependabot, which receive no secrets.

On GitLab, `.gitlab-ci.yml` runs the `pages` job from `.gitlab/ci/pages.yml`, which a project's own pipeline can include instead. The job needs GitLab 17.11 or later, plus parallel deployments (Premium) for merge request previews. It builds the site outside the dev shell in the image `BENCH_NODE_IMAGE` names with the pnpm `BENCH_PNPM_VERSION` pins, and the `pages-pnpm` and `pages-node` flake checks tie both pins to the dev shell, as [Development](development.md#toolchain-and-pins) describes. The job publishes the default branch and a one-week preview under `mr-<iid>/` for each merge request. Astro reads the site's URL and base path from `CI_PAGES_URL` and repository links from `CI_PROJECT_URL`. Checks stay in GitHub Actions, and a Pages build leaves out the vote page, which needs the voting Worker.

## Workflow Steps

Steps that need the dev shell prefix commands with `nix develop -c`. Every multiline script uses a literal block and sets `shell: bash` so actionlint can run shellcheck.

Write multiline step text as one heredoc, never as repeated `printf` or `echo >>` calls.

When the text includes a command's output, read the output into a variable first, since a failed command substitution inside a heredoc does not fail the step under `bash -e`.
