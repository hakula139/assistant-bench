# Testing

[AGENTS.md](../../AGENTS.md#verification) lists the complete verification commands. Run Python checks inside the dev shell so tests of committed systems use the real nixos-config export.

## Placement

Python tests mirror package paths under `tests/`. Shared builders and stub endpoints live in `tests/support.py`, imported as `support`, and data fixtures in `tests/fixtures/`. Group a function's tests in its named class, in module order, nesting method classes under their type.

Vitest tests live beside their modules, with page and built-site tests under `web/src/test/` because Astro treats every file in `pages/` as a route. Build a given site root only once at a time to avoid racing Astro's cache.

## Models and Harbor

Tests isolate the working directory, reports, data cache and endpoint environment from real runtime state. Use invented fixture names and the `endpoint` fixtures for model requests. D1 tests use local databases or mocked transports. No test may read local credentials or call a real model.

Most tests use the synthetic `export` fixture. CLI and committed-system checks also validate the dev shell's real export. Harbor upgrades require real host and Docker trials, including CLI reuse and inspection of logs for operations Harbor may refuse without failing the job.

Vote-store fixtures apply `web/migrations/` to disposable databases. Worker tests run the same schema under the `local` environment, and command tests substitute local stores for cloud connections.

## Cross-Checks and the Report Fixture

The ranking fit is checked against statsmodels, including unanimous wins. Python and site tests share synthetic voted and test-scored rounds in `tests/fixtures/`, plus `report.schema.json` for cross-language schema checks.

When a schema or scoring change makes generated fixtures stale, run `uv run pytest --update-fixtures`, inspect the resulting diff and commit it with the change. Fixture updates run in one process because other workers would read the files being rewritten.

## Accessibility

`site.test.ts` builds in an isolated root with a closed synthetic round so system pages and hydrated charts are included. It serves the built files and a vote API pair from `web/src/test/arena.ts` through Playwright routes, and aborts every other request. On each page in Chromium, under the light and dark color schemes and after the page's islands hydrate, it runs axe-core's default rules and `target-size`, and fails on any violation, page error, or console error. The vote page is checked on a text pair with its first Issues list open. `PlotFigure.test.tsx` also runs axe-core on a drawn chart in jsdom, which checks its ARIA but not its colors. [Quickstart](../guide/quickstart.md#site-verification) covers browser installation.

## Docker Trials

Tests marked `docker` run real Harbor jobs against stub endpoints. Cover oracle and nop validation, the terminal agent, reuse without another request, and tests-only regrading that preserves the solution's identity and diff. Verify request settings and keep keys out of job files.

Installed-agent trials download fixed official CLI releases, verify their published checksums, and exercise tool execution, native trajectories, usage, and diff collection. Only their dedicated fixture registers the exact HTTPS GET release and asset paths with the network guard. Model transports remain loopback-only.

`pyproject.toml` deselects Docker tests by default, and Harbor Smoke selects them with `uv run pytest -m docker`. They need a running Docker daemon and build the smoke task's image on first use.

## Parallel Runs

Tests must not depend on execution order or another test's state. Bind a listening subprocess to port 0 and report its actual port, since probing and releasing a port races other workers. A timed-out worker can terminate without teardown, so keep its resources isolated.

Pass `-n 0` for a targeted run, which otherwise pays every worker's startup, and set `PYTEST_XDIST_AUTO_NUM_WORKERS` to cap the workers on a shared host. `--update-fixtures` always runs in one process, since tests on other workers read the fixtures it rewrites.

## Coverage

`uv run pytest` reports coverage and writes `coverage.xml`, and `pnpm test` measures the site the same way and writes `web/coverage/lcov.info`, leaving out the shadcn/ui copies and the test helpers. The Python report leaves out each `case _: assert_never(...)` arm, which mypy proves unreachable. [CI](ci.md#checks) uploads both to Codecov.
