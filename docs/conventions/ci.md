# CI

## Checks

GitHub Actions runs on pushes to `main`, pull requests, and manual dispatch. The shared devkit workflows own Nix checks, Python and Node dependency setup, tests, caching, and coverage uploads. Project jobs keep the checks and deployment steps specific to the bench.

| Job                           | Responsibility                                                                       |
| ----------------------------- | ------------------------------------------------------------------------------------ |
| nix / Nix (Linux)             | Flake checks, including the GitLab Pages pins, and all pre-commit hooks.             |
| nix / Nix (macOS)             | Flake checks and the native Darwin dev shell build.                                  |
| python / Python Check (Linux) | Python tests and Python coverage upload.                                             |
| node / Node Check (Linux)     | Site and voting Worker tests and web coverage upload.                                |
| Bench Check                   | Editor types, closed-report fingerprints, Docker trials, and affected coding tasks.  |
| Web Check                     | Site lint, types, build, voting Worker dry-run bundle, and the `site-dist` artifact. |
| Deploy Preview                | Publish a static PR preview after Node and Web checks pass.                          |
| CI Result                     | Wait for all checks and the preview, then fail on any failed or cancelled job.       |

Bench Check reads fingerprints from the checked-out tree and the closed reports under `reports/`, enforcing [Systems](../design/systems.md#fingerprints). Its coding-task selector compares the PR base or previous `main` head with the current tree. Added and renamed tasks are checked, while deleted tasks need no trial. Changes to shared code, configuration, dependencies, or the workflow check every coding task. Manual runs also check every task. The core Docker trials run for every event and use the automatic read-only GitHub token to fetch pinned agent releases.

The `main` ruleset requires CI Result and both Codecov patch statuses: `codecov/patch` for Python and `codecov/patch/web` for the site. `codecov.yml` scopes them to `src/` and `web/`, sets an 80% changed-line threshold, and waits for both uploads. Python and Node request `id-token: write` for Codecov's OIDC upload, while fork pull requests use tokenless uploads. The shared workflows may receive `CACHIX_AUTH_TOKEN` for their cache.

List each check job in CI Result's `needs`. Its shared result action inspects the workflow's job results through the Actions API, including matrix jobs cancelled before starting. Waiting for Deploy Preview makes a preview failure part of the gate regardless of when it finishes. Fork and Dependabot previews are skipped because those PRs receive no deploy secrets.

Set each project's job timeout to several times its measured successful duration, including a full coding-family check. `pnpm test` installs Playwright's Chromium headless shell through the web package's `pretest` script. It uses the browser cache and leaves system-library installation to the host, since runner apt downloads have stalled past the job timeout.

## Deploy

Production and PR previews consume Web Check's `site-dist` artifact. `web/wrangler.toml` owns their Worker and database bindings. Deploy secrets are scoped to the steps that run Wrangler.

- **Deploy Voting:** runs after CI Result on `main`, in the `production` environment. Its concurrency group retains up to 100 pending deploys and lets a running deploy finish. Before deploying, it compares the commit with the current `main` head and skips a superseded commit. It applies pending D1 migrations before deploying the voting Worker and its site assets.
- **Deploy Preview:** waits for Node and Web checks on same-repository PRs. It publishes an asset-only Worker Preview named `pr-<number>` and comments the stable preview URL, the commit's deployment URL, and the workflow logs. Its `workers.dev` URL sends `X-Robots-Tag: noindex`. The `preview-<number>` concurrency group is shared with cleanup. A deploy checks that the PR is still open after joining that group, so one starting after cleanup skips deployment.
- **Preview Cleanup:** runs on PR closure through `pull_request_target`, deletes the preview, deactivates its deployments, and updates the comment. It uses the trusted default-branch workflow and checkout because the event carries secrets, including for PRs with merge conflicts. Changes to cleanup take effect once merged.

All three jobs use `CLOUDFLARE_API_TOKEN`, with the account configured in Wrangler. The token needs Workers Scripts Edit and D1 Edit. Applying migrations can create the `d1_migrations` table even when no migration is pending. Worker secrets configured through [Running](../guide/running.md#setting-up-a-vote-store) persist across deploys. Static previews have no voting bindings.

### GitLab Pages

`.gitlab-ci.yml` includes `.gitlab/ci/pages.yml`, which other project pipelines can also include. Pages needs GitLab 17.11 or later and Premium parallel deployments for MR previews. It builds outside the dev shell using `BENCH_NODE_IMAGE` and `BENCH_PNPM_VERSION`. The flake's `pages-node` and `pages-pnpm` checks keep those pins aligned with the dev shell, as [Development](development.md#toolchain-and-pins) describes.

Pages publishes the default branch and a one-week preview under `mr-<iid>/` for each MR. Astro reads the site URL and base path from `CI_PAGES_URL` and repository links from `CI_PROJECT_URL`. Checks run in GitHub Actions. Pages omits the vote page because it needs the voting Worker.

## Workflow Steps

Every multiline script uses a literal block and sets `shell: bash` so actionlint can run shellcheck. Multiline step text is one heredoc, and a command's output enters it through a variable set beforehand, since a failed command substitution inside a heredoc does not fail the step under `bash -e`.
