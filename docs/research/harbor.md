# Harbor

[Harbor](https://github.com/harbor-framework/harbor) runs agents on tasks in containers, verifies them with each task's tests, and records every trial. The bench pins release 0.24.0, which these findings were checked against, and drives it through job configs built from Harbor's own pydantic models.

## What the Bench Uses

- **Job configs:** `JobConfig` with one `AgentConfig` per system, `n_attempts` for samples, `RetryConfig(max_retries=1)` because Harbor defaults to no retries, and a `concurrency_group` with `n_concurrent` per route.
- **Agents:** a `BaseAgent` subclass with an `options_model`, which Harbor validates against the agent's `kwargs` in `preflight` before any trial starts. Its `AgentContext` takes token counts and free-form `metadata`.
- **Verification:** `VerifierConfig(disable=True)` for case families, and Harbor's `oracle` agent, which runs each task's `solution/solve.sh`, for `bench check`.
- **Reruns:** `SourceJobConfig(action='diff')`, which reuses finished trials from earlier jobs.

## Quirks of the Pinned Release

- A task directory without an `environment/` subdirectory loads as a dataset, so each rendered task gets an empty one.
- A task without tests loads only while the verifier is disabled.
- Harbor runs task scripts in the main container through bash, so every image needs it: Alpine fails with exit code 127, and the bench's stock image is Debian slim.
- A diff source reuses a trial only when the task declares a semantic version and every other input, including the agent's `kwargs`, is equal. Because a per-run value in `kwargs` would rerun every trial, `Generate` reads the nixos-config rev at run time and records it in `metadata`.
- A reused trial is skipped and stays in its source job, without a copy in the new one.
- A retry deletes the failed attempt's directory, so only the final attempt of a trial remains.
- `AgentContext.n_input_tokens` includes the cached tokens counted in `n_cache_tokens`.
- Harbor reads telemetry consent only from `HARBOR_TELEMETRY`, which the bench sets to `off` for every run.
- `config.json` in each job directory records every agent's `kwargs` and `env` in plain text.
- The package has no `py.typed` marker, so mypy needs an override to follow its annotations.
