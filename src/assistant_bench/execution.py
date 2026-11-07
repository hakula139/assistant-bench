"""Harbor lifecycle fences for disposable, cloud-owned executions."""

import asyncio
import json
import os
import subprocess
import tempfile
from contextlib import ExitStack
from contextvars import Token
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from harbor.job import Job
from harbor.job_diff import DiffAction, compare_job_trials, load_source_trials
from harbor.job_plan import JobPlan
from harbor.models.job.config import JobConfig, SourceJobConfig
from harbor.models.job.plugin import BaseJobPlugin
from harbor.models.job.result import JobResult
from harbor.models.trial.config import TrialConfig
from harbor.trial.hooks import TrialHookEvent
from pydantic import BaseModel, ConfigDict

from assistant_bench import jobs
from assistant_bench.arena.d1 import connect, token as d1_token
from assistant_bench.artifacts import ArtifactRepository, build_manifest, snapshot_trial
from assistant_bench.harnesses.accounting import (
    TrialAccounting,
    bind_trial,
    reset_trial,
)
from assistant_bench.harnesses.endpoint import HEADERS_VAR, KEY_FILE_VAR
from assistant_bench.messages import InputError
from assistant_bench.runtime import RunContext
from assistant_bench.spend import Accounting, Entry
from assistant_bench.state import Claim, Run, State


class ExecutionInput(BaseModel):
    """Scratch configuration contains business identities and frozen prices only."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    run: Run
    accounting: dict[str, Accounting]


def execute(
    context: RunContext,
    config: JobConfig,
    accounting: dict[str, Accounting],
    *,
    scope: str,
    run_id: str,
    log: Path | None = None,
) -> Run:
    run = Run(
        id=run_id,
        scope=scope,
        job=config.job_name,
        code_revision=context.code_revision,
        case_revision=context.case_revision,
        created_at=datetime.now(UTC).isoformat(),
    )
    context.state.create_run(run)
    path = context.root / f'execution-{run.id}.json'
    path.write_text(ExecutionInput(run=run, accounting=accounting).model_dump_json())
    try:
        jobs.run_job(config, log=log, plugin_context=path)
    except BaseException as error:
        _cancel_unstarted(context.state, run.id, error)
        raise
    if not context.state.complete_run(run.id):
        raise InputError('The execution has unsettled work and cannot complete.')
    return run


class ExecutionPlugin(BaseJobPlugin):
    def __init__(self, *, context_file: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._root = Path(context_file).parent
        self._input = ExecutionInput.model_validate_json(Path(context_file).read_text())
        self._stack = ExitStack()
        self._state = State(self._stack.enter_context(connect('private')))
        self._claims: dict[str, Claim] = {}
        self._recorders: dict[
            UUID, tuple[TrialAccounting, Token[TrialAccounting | None]]
        ] = {}
        self._regrades: set[str] = set()
        self._job_dir: Path | None = None

    async def on_job_start(self, job: Job) -> None:
        if job.is_resuming or job.config.job_name != self._input.run.job:
            raise InputError('Cloud executions require a fresh matching Harbor job.')
        if self._state.run(self._input.run.id) != self._input.run:
            raise InputError('The execution metadata differs from its cloud record.')
        self._job_dir = job.job_dir
        plan = await JobPlan.from_config(
            job.config.model_copy(deep=True), job_id=job.id
        )
        sources: list[tuple[SourceJobConfig, Path]] = []
        for source in job.config.source_jobs:
            if source.type != 'local' or source.path is None:
                raise InputError(
                    'Cloud execution sources must be materialized local jobs.'
                )
            sources.append((source, source.path))
        diff = compare_job_trials(plan, load_source_trials(sources))
        expected = [
            (index, config, change)
            for index, (config, change) in enumerate(
                zip(plan.trial_configs, diff.trials, strict=True)
            )
            if change.action != DiffAction.REUSE
        ]
        actual = tuple(job._remaining_trial_configs)  # pyright: ignore[reportPrivateUsage]
        if len(actual) != len(expected):
            raise InputError('Harbor changed the planned execution inventory.')
        for configured, (_, target, change) in zip(actual, expected, strict=True):
            if change.action == DiffAction.RERUN:
                matches = not configured.is_regrade and configured == target
            else:
                source_trial = configured.source_trial
                matches = (
                    source_trial is not None
                    and change.source is not None
                    and source_trial.trial_id == change.source.id
                    and source_trial.path == change.source.trial_dir
                    and target.model_copy(update={'source_trial': source_trial})
                    == configured
                )
            if not matches:
                raise InputError('Harbor changed a planned trial before admission.')

        revision = self._state.source_revision(self._input.run.scope)
        try:
            for configured, (index, _, change) in zip(actual, expected, strict=True):
                if change.action == DiffAction.REGRADE:
                    self._regrades.add(configured.trial_name)
                    continue
                claim = self._claim(
                    configured,
                    change.task_name,
                    index // (len(plan.task_configs) * len(job.config.agents)),
                    revision,
                )
                if not self._state.claim_attempt(claim):
                    raise InputError(
                        'Another execution already owns a planned paid slot.'
                    )
                self._claims[configured.trial_name] = claim
        except BaseException as error:
            _cancel_unstarted(self._state, self._input.run.id, error)
            raise
        job.on_trial_started(self._started)
        job.on_trial_ended(self._ended)

    def _claim(
        self, config: TrialConfig, case: str, ordinal: int, revision: int
    ) -> Claim:
        system = str(config.agent.kwargs['system'])
        fingerprint = str(config.agent.kwargs['fingerprint'])
        return Claim(
            id=str(uuid4()),
            run_id=self._input.run.id,
            scope=self._input.run.scope,
            slot_key=json.dumps([case, system, ordinal], separators=(',', ':')),
            trial=config.trial_name,
            system=system,
            fingerprint=fingerprint,
            accounting_json=self._input.accounting[system].model_dump_json(),
            expected_source_revision=revision,
            created_at=datetime.now(UTC).isoformat(),
        )

    async def _started(self, event: TrialHookEvent) -> None:
        if event.trial_name in self._regrades:
            return
        claim = self._claims[event.trial_name]
        if (
            self._state.get_claim(claim.id) != claim
            or self._state.claim_state(claim.id) != 'claimed'
            or event.config.agent.kwargs['system'] != claim.system
            or event.config.agent.kwargs['fingerprint'] != claim.fingerprint
        ):
            raise InputError('A trial no longer owns its original paid claim.')
        recorder = TrialAccounting(self._state, claim)
        self._recorders[event.trial_id] = (recorder, bind_trial(recorder))

    async def _ended(self, event: TrialHookEvent) -> None:
        if event.trial_name in self._regrades:
            await self._publish(event, ())
            return
        recorder, token = self._recorders.pop(event.trial_id)
        try:
            claim = self._claims[event.trial_name]
            attempt = recorder.attempt()
            if attempt is None:
                if not self._state.cancel_claim(claim.id):
                    raise InputError(
                        'A trial that never started could not release its claim.'
                    )
                return
            accounting = self._input.accounting[claim.system]
            entry = Entry(
                **attempt.model_dump(),
                job=self._input.run.job,
                price=accounting.price,
                ceiling_usd=accounting.ceiling_usd,
            )
            self._state.record_entry(claim.id, entry.model_dump_json())
            if recorder.fatal:
                raise InputError('A trial has uncertain accounting and cannot settle.')
            await self._publish(event, recorder.secrets())
            self._state.settle_claim(claim.id)
        except BaseException:
            self._state.mark_uncertain(recorder.claim_id)
            raise
        finally:
            reset_trial(token)

    async def _publish(self, event: TrialHookEvent, secrets: tuple[str, ...]) -> None:
        if self._job_dir is None:
            raise InputError('The execution has no initialized Harbor job directory.')
        known = set(secrets)
        known.add(d1_token())
        for agent in event.config.agent.env:
            if any(
                word in agent.upper() for word in ('TOKEN', 'SECRET', 'PASSWORD', 'KEY')
            ):
                known.add(event.config.agent.env[agent])
        if key_file := os.environ.get(KEY_FILE_VAR):
            known.add(Path(key_file).read_text().strip())
        if headers := os.environ.get(HEADERS_VAR):
            known.update(json.loads(headers).values())
        run = self._input.run
        repository = ArtifactRepository(os.environ['BENCH_DATA_REPOSITORY'])
        artifact_id = str(uuid4())
        destination = self._root / 'snapshots' / artifact_id / run.job
        source = self._job_dir

        def publish() -> tuple[str, str, str]:
            snapshot_trial(source, destination, event.trial_name)
            _audit(destination, known)
            manifest = build_manifest(
                destination,
                scope=run.scope,
                run_id=run.id,
                artifact_id=artifact_id,
                trial_id=str(event.trial_id),
                job_name=run.job,
                trial_name=event.trial_name,
                code_revision=run.code_revision,
                case_revision=run.case_revision,
            )
            reference = repository.publish_trial(
                run.id, artifact_id, destination, manifest
            )
            return reference.commit_sha, reference.path, reference.manifest_digest

        commit, path, digest = await asyncio.to_thread(publish)
        self._state.record_trial_artifact(
            run.id, event.trial_name, str(event.trial_id), commit, path, digest
        )

    async def on_job_end(self, job_result: JobResult) -> None:
        self._stack.close()


def _audit(root: Path, secrets: set[str]) -> None:
    known = [value.encode() for value in secrets if value]
    selected: list[tuple[Path, bytes]] = []
    for path in root.rglob('*'):
        if path.is_symlink():
            content = os.fsencode(os.readlink(path))
        elif path.is_file():
            content = path.read_bytes()
        else:
            continue
        location = os.fsencode(path.relative_to(root))
        if any(value in content or value in location for value in known):
            raise InputError(
                'A selected artifact contains a known credential. Publication refused.'
            )
        selected.append((path.relative_to(root), content))
    environment = {
        name: value
        for name, value in os.environ.items()
        if name not in ('GITLEAKS_CONFIG', 'GITLEAKS_CONFIG_TOML')
    }
    try:
        with tempfile.TemporaryDirectory(prefix='bench-audit-') as temporary:
            directory = Path(temporary)
            audit = directory / 'files'
            for relative, content in selected:
                path = audit / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            result = subprocess.run(
                [
                    'gitleaks',
                    'dir',
                    '--redact=100',
                    '--no-banner',
                    '--no-color',
                    '--ignore-gitleaks-allow',
                    '--exit-code=42',
                    '--max-target-megabytes=0',
                    str(audit),
                ],
                cwd=directory,
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=300,
                check=False,
            )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise InputError(
            'The artifact audit could not run `gitleaks` from the development shell.'
        ) from error
    if result.returncode:
        raise InputError(
            'The artifact credential scan did not pass. Publication refused.'
        )


def _cancel_unstarted(state: State, run_id: str, error: BaseException) -> None:
    try:
        state.cancel_unstarted(run_id)
    except Exception:
        error.add_note(
            f'Cleanup of run `{run_id}` could not be confirmed. '
            'Its paid claims remain blocked until inspected.'
        )
