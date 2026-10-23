# Harbor

[Harbor](https://github.com/harbor-framework/harbor) runs agents on tasks in containers, verifies them with each task's tests, and records every trial. The bench pins release 0.24.0, which these findings were checked against, and drives it through job configs built from Harbor's own pydantic models.

## What the Bench Uses

- **Job configs:** `JobConfig` with one `AgentConfig` per system, `n_attempts` for samples, `RetryConfig(max_retries=1)` because Harbor defaults to no retries, and a `concurrency_group` with `n_concurrent` per route.
- **Agents:** a `BaseAgent` subclass with an `options_model`, which Harbor validates against the agent's `kwargs` in `preflight` before any trial starts. Its `AgentContext` takes token counts and free-form `metadata`.
- **Verification:** `VerifierConfig(disable=True)` for case families, and Harbor's `oracle` agent, which runs each task's `solution/solve.sh`, beside its `nop` agent, which does nothing, for `bench check`. A task's separate verifier receives its `/app` artifact, and `network_mode = "no-network"` applies to the agent and verifier phases, which `resolve_trial_network_plan` resolves from the task's baseline and phase overrides.
- **Reruns:** `SourceJobConfig(action='diff')`, which reuses finished trials from earlier jobs.

## Quirks of the Pinned Release

- A task without tests loads only while the verifier is disabled.
- Harbor runs task scripts in the main container through bash, so every image needs it: Alpine fails with exit code 127, and the bench's stock image is Debian slim.
- A diff source reuses a trial only when the task declares a semantic version and every other input is equal. Those inputs include the whole agent config, so changing a system's concurrency limit or its `system` kwarg, which a table rename does, reruns its trials. Because a per-run value in `kwargs` would rerun every trial, `Generate` reads the nixos-config rev at run time and records it in `metadata`.
- Each trial's `lock.json` records its task's digest, the `sha256:` of `Packager.compute_content_hash` over the task's files. A source and target task with the same version but different digests abort the whole job. A patch-level version bump reuses the earlier trials, a minor bump regrades them, and a major bump reruns them. A version that differs only in its pre-release or build metadata reruns, which Harbor's own comment calls outside its supported change levels.
- The bench puts a hash of its own task inputs in the build metadata, so an edited case reruns under its ID. It hashes only what it controls, so a Harbor upgrade that changes a default in `task.toml` leaves every version alone while the digests change, and the job aborts until the family's earlier job directories are removed.
- A source trial that errored is rerun into the new job, while the failed trial stays in its source job.
- `harbor job resume` reuses the job's `config.json` but runs the current code, so a resumed `Generate` trial records the rev of the current export against the system prompt the job was created with.
- A reused trial is skipped and stays in its source job, without a copy in the new one.
- A retry deletes the failed attempt's directory, so only the final attempt of a trial remains.
- `AgentContext.n_input_tokens` includes the cached tokens counted in `n_cache_tokens`.
- Harbor reads telemetry consent only from `HARBOR_TELEMETRY`, which the bench sets to `off` for every run.
- `config.json` in each job directory records every agent's `kwargs` and `env` in plain text.
- The package has no `py.typed` marker, so mypy needs an override to follow its annotations.
