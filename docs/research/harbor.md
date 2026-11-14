# Harbor

[Harbor](https://github.com/harbor-framework/harbor) runs agents on tasks in containers, verifies them with each task's tests, and records every trial. The bench pins one release in `pyproject.toml` and drives it through job configs built from Harbor's own pydantic models.

## Quirks of the Pinned Release

### Tasks and Reuse

- A task without tests loads only while the verifier is disabled.
- A task without an `environment/` directory loads through `TaskConfig(path=...)`, since only the commands that scan directories for tasks check for it, and only Docker's own definition check needs an image or a Dockerfile.
- A diff source reuses a trial only when the task declares a semantic version and every other input is equal. Those inputs include the whole agent config and the job-wide `JobConfig.environment`, which every trial's lock records, so changing a system's concurrency limit or its `system` kwarg, which a table rename does, reruns its trials, and changing the environment reruns every trial of the job whatever the task versions say. Because a per-run value in `kwargs` would rerun every trial, `Generate` and `Terminus` read the nixos-config rev at run time and record it in `metadata`.
- Each trial's `lock.json` records its task's digest, the `sha256:` of `Packager.compute_content_hash` over the task's files. A source and target task with the same version but different digests abort the whole job. Harbor reuses, regrades, or reruns the earlier trials by the [bump level](../guide/authoring.md#tasks). A version that differs only in its pre-release or build metadata reruns, which Harbor's own comment calls outside its supported change levels.
- A Harbor upgrade that changes a `task.toml` default can change the digests without changing task versions and abort the job. [Architecture](../design/architecture.md#reruns) describes rendered task versions.
- A regrade is a new trial seeded from the source's agent logs, context and artifacts, with symlinks dereferenced. It records its source in `config.source_trial`. Harbor prefers the original trial while its job remains and can regrade it twice, including when `samples` rises after a regrade. It regrades only a source that finished without an error and with a reward.
- An `AgentTimeoutError` or a non-zero agent exit is recorded on the trial, after which Harbor still collects the artifacts and runs the verifier, so the trial has a reward and an error at once.
- A source trial that errored is rerun into the new job, while the failed trial stays in its source job.
- `harbor job resume` reuses the job's `config.json` but runs the current code, so a resumed trial records the rev of the current export, while a resumed `Generate` trial still sends the system prompt the job was created with.
- A reused trial is skipped and stays in its source job, without a copy in the new one.
- A retry deletes the failed attempt's directory, so only the final attempt of a trial remains.

### Execution and Isolation

- Harbor runs task scripts through bash in the container that runs them, including a separate verifier, so every image needs bash. Alpine fails with exit code 127.
- A trial reads logs and artifacts in place when an enabled bind mount maps their environment path to the expected host path. Other entries are downloaded, and Harbor logs and ignores a failed transfer.
- `record_terminal_session` defaults to on and records with asciinema, which a trial cannot install without a network, so the bench turns it off.
- `config.json` in each job directory records every agent's `kwargs` and `env` in plain text.

### Metadata and Transports

- `AgentContext.n_input_tokens` includes the cached tokens counted in `n_cache_tokens`.
- Harbor reads telemetry consent only from `HARBOR_TELEMETRY`, LiteLLM loads a `.env` above its own install unless `LITELLM_MODE=PRODUCTION`, and importing LiteLLM downloads its cost map unless `LITELLM_LOCAL_MODEL_COST_MAP=true`, so `bench` sets all three in its own environment, which Harbor inherits.
- terminus-2 stops only after the model repeats `task_complete: true`, so one completed trial makes at least two model calls.
- terminus-2 adds Anthropic `cache_control` blocks whenever the model name contains `anthropic` or `claude`, so an OpenAI-compatible route for an Anthropic model must accept them.
- Both `llm_kwargs` and `llm_call_kwargs` reach every LiteLLM completion call.
- The native client enters through a private Terminus method, so upgrading this integration needs the [real Harbor trials](../conventions/python.md).
