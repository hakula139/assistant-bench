# Testing

Conventions for tests, their environment, Docker trials, and test coverage.

## Placement

- Tests mirror the package: `tests/test_<module>.py` for each module and `tests/<subpackage>/test_<module>.py` for each subpackage member, such as `tests/harnesses/test_generate.py`. `pyproject.toml` sets pytest's `--import-mode=importlib` so same-named test files in different directories do not collide.
- `test_repository.py` checks committed data and lockfiles, while `test_harbor_smoke.py` holds the trials that need Docker.
- Fixtures live in `tests/conftest.py`, data fixtures in `tests/fixtures/`, and builders such as `make_system` and the stub endpoint in `tests/support.py`, which tests import as `support`.

## Grouping and Naming

- Group each function's tests into a PascalCase class named after it, as in `TestResolveSystem`, so `pytest -k TestResolveSystem` runs them, ordering the classes to match the module's function order.
- Nest a method's class in a class named after its type, as in `TestSystem.TestFingerprint`.
- Name each test for the scenario and outcome in domain terms, omitting the function name because the enclosing class carries it, as in `test_applies_the_default_effort_a_model_requires`.
- Structure each test into arrange, act, and assert steps separated by blank lines, keeping any check that the act succeeded beside the act.

## Data and Infrastructure

- Use generic, fictional data such as `acme/large` and `example.com`, applying real model names only where the code matches on them, as the committed `systems.toml` test does.
- An autouse fixture points `XDG_DATA_HOME` at a scratch directory and runs each test from it, ensuring no test reads the real data directory or a local `.env`.
- Reach models only through the `endpoint` fixture, a chat-completions server on 127.0.0.1 that records each request and returns a configurable response.
- The `export` fixture points `BENCH_NIXOS_CONFIG` at `tests/fixtures/nixos-config.json`, while the CLI tests and the test of the committed `systems.toml` read the dev shell's real export, so the suite runs inside `nix develop`.
- CLI tests replace `jobs.run_job` with a recorder to check the submitted job config without starting Harbor.

## Docker Trials

Tests marked `docker` run real Harbor jobs: the smoke family's oracle, and `Generate` on the `smoke-text` cases against the stub endpoint, executed twice to check that the rerun sends nothing. `pyproject.toml` deselects them by default, and the Harbor Smoke CI job selects them:

```bash
uv run pytest -m docker
```

These trials require a running Docker daemon and pull a stock image on first use, while every other test runs offline.

## Assertions

- Each assertion should fail when the code under test has a plausible bug. Compare whole values, such as a request body or an output record, so an unexpected field fails the test.
- Aim for a concise suite with full coverage, merging tests that cover the same path.

## Coverage

`uv run pytest` reports coverage with the missing lines and writes `coverage.xml`, which CI uploads to Codecov to report patch coverage in a PR comment. Cover changed lines with tests that check behavior. Leave a line uncovered only when it is wiring that only the Docker trials reach, and state so in the PR's Test plan.
