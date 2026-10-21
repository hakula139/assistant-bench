# Testing

Where tests live, how they reach models and Harbor, and what coverage they owe.

## Placement

- Tests mirror the package: `tests/test_<module>.py` for each module and `tests/<subpackage>/test_<module>.py` for each subpackage member, such as `tests/harnesses/test_generate.py`. `pyproject.toml` sets pytest's `--import-mode=importlib` so that same-named test files in different directories do not collide.
- The site's Vitest tests sit beside their modules as `web/src/**/*.test.{ts,tsx}`, except the page tests in `web/src/test/`, since Astro routes every file under `pages/`. `web/src/test/fixture.ts` reads the report fixture for them, and tests that need a DOM opt into jsdom with a `@vitest-environment` comment.
- `test_repository.py` checks committed data and lockfiles, and `test_harbor_smoke.py` holds the trials that need Docker.
- Fixtures live in `tests/conftest.py`, data fixtures in `tests/fixtures/`, and builders such as `make_system`, `write_trial`, `make_arena`, `write_synthetic_round`, and the stub endpoint in `tests/support.py`, which tests import as `support`.
- Each function's tests sit in a class named after it, as in `TestResolveSystem`, with the classes in the module's function order. A method's class nests in a class named after its type, as in `TestSystem.TestFingerprint`.

## Models and Harbor

- Fixtures use generic names such as `acme/large` and `example.com`. Real model names appear only where the code matches on them, as in the test of the committed `systems.toml`.
- An autouse fixture points `XDG_DATA_HOME` at a scratch directory and runs each test from it, so no test reads the real data directory or a local `.env`.
- Tests reach models only through the `endpoint` fixture, a chat-completions server on 127.0.0.1 that records each request and returns a configurable response, or `tls_endpoint`, the same server behind a test CA.
- The `export` fixture points `BENCH_NIXOS_CONFIG` at `tests/fixtures/nixos-config.json`. The CLI tests and the test of the committed `systems.toml` read the dev shell's real export, so the suite runs inside `nix develop`.
- CLI tests replace `jobs.run_job` with a recorder to check the submitted job config without starting Harbor, and `write_trial` writes the trial records Harbor would, including the `lock.json` its diff reads.

## Cross-Checks and the Report Fixture

- The ranking fit is checked against statsmodels' `Logit` on simulated votes, so a change to the loss, the design, or the penalty that moves the estimates fails.
- `tests/fixtures/report.json` and `report-early.json` are the reports `write_synthetic_round` closes to after ten sessions and after one, which the site's tests also parse and render. Their test rewrites a file when a model or the ranking changes it, so review the diff and commit the new fixture with the change.

## Docker Trials

Tests marked `docker` run real Harbor jobs: the smoke family's oracle, and `Generate` on the `smoke-text` cases against the stub endpoint, run twice to check that the rerun sends nothing. `pyproject.toml` deselects them by default, and the Harbor Smoke CI job selects them with `uv run pytest -m docker`. They need a running Docker daemon and pull a stock image on first use.

## Coverage

`uv run pytest` reports coverage with the missing lines and writes `coverage.xml`, which CI uploads to Codecov for patch coverage. A line may stay uncovered only when the Docker trials alone reach it, which the PR's Test plan states.
