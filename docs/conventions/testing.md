# Testing

Where tests live, when they merge or go, how they reach models and Harbor, and what coverage they owe.

## Placement

- Tests mirror the package: a module's tests live in `tests/test_<module>.py`, and a subpackage member's in `tests/<subpackage>/test_<module>.py`, such as `tests/harnesses/test_generate.py`. `pyproject.toml` sets pytest's `--import-mode=importlib` so that same-named test files in different directories do not collide.
- The site's Vitest tests sit beside their modules as `web/src/**/*.test.{ts,tsx}`, except the page tests in `web/src/test/`, since Astro routes every file under `pages/`. `web/src/test/fixture.ts` reads the report fixture for them, and tests that need a DOM opt into jsdom with a `@vitest-environment` comment. `web/src/test/plot.ts` stubs what `PlotFigure` needs to draw a chart in jsdom.
- `test_repository.py` checks committed data and lockfiles, and `test_harbor_smoke.py` holds the trials that need Docker.
- Fixtures live in `tests/conftest.py`, data fixtures in `tests/fixtures/`, and builders such as `make_system`, `write_trial`, `make_arena`, `write_synthetic_round`, and the stub endpoint in `tests/support.py`, which tests import as `support`.
- Each function's tests sit in a class named after it, as in `TestResolveSystem`, with the classes in the module's function order. A method's class nests in a class named after its type, as in `TestSystem.TestFingerprint`.

## Merging and Dropping Tests

Tests that run the same steps and assertions on different values make one parametrized test whose name states the rule they share. In Python, that test uses `pytest.mark.parametrize` with `ids` wherever pytest's own would not name the case, and in Vitest, `test.each` with a title that names each case. Tests whose arrange steps or assertions differ stay apart, even when they call the same function.

Drop a test once another covers everything it checks.

## Models and Harbor

- Fixtures use generic names such as `acme/large` and `example.com`. Real model names appear only where the code matches on them, as in the test of the committed `systems.toml`.
- An autouse fixture points `BENCH_DATA_DIR` at a scratch directory and runs each test from it, so no test reads the real data directory or a local `.env`.
- Tests reach models only through the `endpoint` fixture, a chat-completions server on 127.0.0.1 that records each request and returns a configurable response, or `tls_endpoint`, the same server behind a test CA, or `ipv6_endpoint`, the same server on an IPv6 address.
- The `export` fixture points `BENCH_NIXOS_CONFIG` at `tests/fixtures/nixos-config.json`. The CLI tests and the test of the committed `systems.toml` read the dev shell's real export, so the suite runs inside `nix develop`.
- CLI tests replace `jobs.run_job` with a recorder to check the submitted job config without starting Harbor, and `write_trial` writes the trial records Harbor would, including the `lock.json` its diff reads. `finish_job` writes a successful trial for each trial Harbor plans for a submitted job, with the locks from Harbor's own plan, so a test can check what a later job's diff reuses.

## Cross-Checks and the Report Fixture

- The ranking fit is checked against statsmodels' `Logit` on simulated votes, so a change to the loss or the design that moves the estimates fails. The penalty barely moves those estimates, so the test of a system that wins every vote bounds its gap tightly enough that a tenfold change to the penalty in either direction fails.
- `tests/fixtures/report.json` and `report-early.json` are the reports `write_synthetic_round` closes to after ten sessions and after one, which the site's tests also parse and render, and `report.schema.json` is the `Report` model's JSON Schema, which the site's tests compare with its Zod schema. Their tests fail when a model or ranking change leaves a file stale, and `uv run pytest --update-fixtures` rewrites it, so review the diff and commit the new fixture with the change.

## Docker Trials

Tests marked `docker` run real Harbor jobs: `bench check` over the smoke family's oracle and nop, `Generate` on the `smoke-text` cases against the stub endpoint, run twice to check that the rerun sends nothing, and `Terminus` on the `smoke` task, which checks the endpoint's headers and token budget and that no job file holds the key. `pyproject.toml` deselects them by default, and the Harbor Smoke CI job selects them with `uv run pytest -m docker`. They need a running Docker daemon and pull a stock image on first use.

## Coverage

`uv run pytest` reports coverage with the missing lines and writes `coverage.xml`, and `pnpm test` measures the site the same way and writes `web/coverage/lcov.info`, leaving out the shadcn/ui copies and the test helpers. The Python report leaves out each `case _: assert_never(...)` arm, which mypy proves unreachable. CI uploads both to Codecov under the `python` and `web` flags. The coverage bar is Codecov's `codecov/patch` check, a required check that fails when tests cover less than 80% of a PR's changed Python lines, as `codecov.yml` sets, and `codecov/patch/web` reports the same bar for the site without being required.
