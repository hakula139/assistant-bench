"""Single-request route canaries, their baselines, and independent spend records."""

import hashlib
import json
import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from harbor.models.job.config import JobConfig
from harbor.models.trial.result import TrialResult
from pydantic import BaseModel, ConfigDict
from pydantic_core import to_jsonable_python

from assistant_bench import execution, jobs
from assistant_bench.artifacts import ArtifactRef
from assistant_bench.catalog import System, SystemIdentity
from assistant_bench.harnesses import fingerprint
from assistant_bench.harnesses.accounting import RequestReceipt
from assistant_bench.harnesses.generate import OUTPUT_FILE
from assistant_bench.harnesses.usage import MeasuredUsage, combined_usage
from assistant_bench.messages import InputError
from assistant_bench.paths import REPO_ROOT
from assistant_bench.runtime import RunContext
from assistant_bench.spend import Accounting
from assistant_bench.state import State
from assistant_bench.tasks import render_task_config


CANARY_MAX_TOKENS = 2048


@dataclass(frozen=True)
class Probe:
    original: System
    source_generate: System
    actual: System
    config: JobConfig
    protocol_digest: str

    @property
    def ceiling_usd(self) -> float | None:
        """Full-context upper cost, unknown when any input write rate is unknown."""
        price = self.actual.price
        if price is None or price.cache_write is None:
            return None
        return price.cost(
            input_tokens=self.actual.context_window,
            cached_tokens=0,
            cache_write_tokens=self.actual.context_window
            if price.cache_write > price.input
            else 0,
            output_tokens=self.actual.max_output_tokens,
        )


class Record(BaseModel):
    """A probe outcome, including failed attempts that cannot establish a baseline."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    system: str
    job: str
    trial: str | None
    time: datetime
    original_identity: SystemIdentity
    source_generate_identity: SystemIdentity
    actual_probe_identity: SystemIdentity
    source_generate_fingerprint: str
    actual_probe_fingerprint: str
    probe_protocol_digest: str
    usage: MeasuredUsage | None
    response_models: list[str]
    output: str
    finish_reason: str | None
    latency_sec: float | None
    error: str | None

    @property
    def success(self) -> bool:
        return (
            self.error is None
            and self.finish_reason == 'stop'
            and self.usage is not None
        )


def prepare(
    configs: Iterable[JobConfig],
    systems: Mapping[str, System],
    *,
    root: Path,
    source_root: Path = REPO_ROOT,
) -> list[Probe]:
    """Prepare one probe for each selected system with at least one pending trial."""
    pending = {name for config in configs for name in jobs.pending_trials(config)}
    if not pending:
        return []

    root = root / 'canaries'
    task = root / 'task'
    task.mkdir(parents=True, exist_ok=True)
    instruction = (source_root / 'src' / 'assistant_bench' / 'canary.md').read_text()
    task_config = render_task_config('canaries/fixed', instruction)
    (task / 'instruction.md').write_text(instruction)
    (task / 'task.toml').write_text(task_config.model_dump_toml())

    probes: list[Probe] = []
    for name in sorted(pending):
        original = systems[name]
        source = original.model_copy(update={'harness': 'generate', 'version': None})
        actual = source.model_copy(
            update={
                'max_output_tokens': min(CANARY_MAX_TOKENS, source.max_output_tokens)
            }
        )
        config = jobs.probe_job(actual, task, root / 'jobs')
        stable_config = _canonical_config(
            config.model_dump(exclude={'job_name', 'jobs_dir', 'tasks'})
        )
        digest = hashlib.sha256(
            json.dumps(
                {
                    'task': task_config.model_dump(mode='json'),
                    'probe_fingerprint': fingerprint(actual),
                    'config': stable_config,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        probes.append(Probe(original, source, actual, config, digest))
    return probes


def _canonical_config(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _canonical_config(item)
            for key, item in cast(dict[str, Any], value).items()
        }
    if isinstance(value, (list, tuple)):
        return [_canonical_config(item) for item in cast(Iterable[Any], value)]
    if isinstance(value, (set, frozenset)):
        return sorted(
            (_canonical_config(item) for item in cast(Iterable[Any], value)),
            key=lambda item: json.dumps(item, sort_keys=True),
        )
    return to_jsonable_python(value)


def run(probes: list[Probe], context: RunContext) -> list[Record]:
    """Record every outcome and its spend before refusing a failed or changed probe."""
    records: list[Record] = []
    problems: list[str] = []
    for probe in probes:
        key = context_key(probe)
        run_id = str(uuid4())
        run_error: str | None = 'InterruptedProbe'
        try:
            execution.execute(
                context,
                probe.config,
                {
                    probe.actual.name: Accounting(
                        system=probe.actual.name,
                        price=probe.actual.price,
                        ceiling_usd=probe.ceiling_usd,
                    )
                },
                scope='canaries',
                run_id=run_id,
                expected_source_revision=context.state.artifact_generation(
                    'canaries'
                ).revision,
                log=probe.config.jobs_dir / f'{probe.config.job_name}.log',
            )
            run_error = None
        except subprocess.CalledProcessError as error:
            run_error = type(error).__name__
        finally:
            context.state.run(run_id)
            outcomes = _outcomes(probe, run_error, context, run_id)
            if len(outcomes) != 1:
                raise InputError('A canary execution produced more than one outcome.')
            context.state.append_canary(run_id, outcomes[0].model_dump_json())

        records.extend(outcomes)
        probe_problems = [
            problem
            for record in outcomes
            if (problem := _problem(record, [])) is not None
        ]
        if not probe_problems:
            context.state.establish_baseline(probe.original.name, key, run_id)
            winner = context.state.baseline(probe.original.name, key)
            if winner is None:
                raise InputError('The accepted canary baseline could not be confirmed.')
            baseline = Record.model_validate_json(winner)
            probe_problems.extend(
                problem
                for record in outcomes
                if (problem := _problem(record, [baseline])) is not None
            )
        problems.extend(probe_problems)
    if problems:
        raise InputError('\n'.join(problems))
    return records


def context_key(probe: Probe) -> str:
    return json.dumps(
        [fingerprint(probe.source_generate), probe.protocol_digest],
        separators=(',', ':'),
    )


def read_records(system: str, state: State) -> list[Record]:
    return [Record.model_validate_json(value) for value in state.records(system)]


def _outcomes(
    probe: Probe, run_error: str | None, context: RunContext, run_id: str
) -> list[Record]:
    directory = jobs.job_path(probe.config)
    results = [
        TrialResult.model_validate_json(path.read_text())
        for path in sorted(directory.glob('*/result.json'))
    ]
    return (
        [_record(probe, result, run_error, context, run_id) for result in results]
        if results
        else [_record(probe, None, run_error or 'MissingTrialResult', context, run_id)]
    )


def _record(
    probe: Probe,
    result: TrialResult | None,
    run_error: str | None,
    context: RunContext,
    run_id: str,
) -> Record:
    directory = jobs.job_path(probe.config)
    state = context.state
    trial = result.trial_name if result else None
    audited = result is not None and state.trial_registered(
        run_id, result.trial_name, str(result.id)
    )
    if audited and result is not None:
        row = state.trial_artifact(run_id, result.trial_name)
        archive = context.artifacts.materialize(
            ArtifactRef(
                commit_sha=cast(str, row['commit_sha']),
                path=cast(str, row['path']),
                manifest_digest=cast(str, row['manifest_digest']),
            ),
            context.root / 'canary-records' / run_id / str(result.id),
        )
        if (
            archive.manifest.run_id != run_id
            or archive.manifest.trial_name != result.trial_name
            or archive.manifest.trial_id != str(result.id)
        ):
            raise InputError(
                'The canary artifact has a conflicting execution identity.'
            )
        frozen = TrialResult.model_validate_json(
            (archive.job / result.trial_name / 'result.json').read_text()
        )
        if frozen.id != result.id or frozen.trial_name != result.trial_name:
            raise InputError('The canary result differs from its registered identity.')
        result = frozen
        directory = archive.job
    receipts = (
        [
            RequestReceipt.model_validate_json(cast(str, row['receipt_json']))
            if row['receipt_json'] is not None
            else None
            for row in state.trial_requests(run_id, trial)
        ]
        if trial is not None
        else []
    )
    agent = result.agent_result if result is not None and audited else None
    metadata = (agent.metadata or {}) if agent else {}
    output = directory / trial / 'agent' / OUTPUT_FILE if trial and audited else None
    exception = result.exception_info if result is not None and audited else None
    return Record(
        system=probe.original.name,
        job=probe.config.job_name,
        trial=trial,
        time=(result.started_at if result else None) or datetime.now(UTC),
        original_identity=SystemIdentity.model_validate(probe.original.model_dump()),
        source_generate_identity=SystemIdentity.model_validate(
            probe.source_generate.model_dump()
        ),
        actual_probe_identity=SystemIdentity.model_validate(probe.actual.model_dump()),
        source_generate_fingerprint=fingerprint(probe.source_generate),
        actual_probe_fingerprint=fingerprint(probe.actual),
        probe_protocol_digest=probe.protocol_digest,
        usage=combined_usage(
            [receipt.usage if receipt else None for receipt in receipts]
        ),
        response_models=sorted(
            {
                receipt.response_model
                for receipt in receipts
                if receipt is not None and receipt.response_model is not None
            }
        ),
        output=output.read_text() if output and output.exists() else '',
        finish_reason=metadata.get('finish_reason'),
        latency_sec=metadata.get('latency_sec'),
        error=(exception.exception_type if exception else run_error)
        if audited
        else 'UnauditedTrial',
    )


def _problem(record: Record, previous: list[Record]) -> str | None:
    if not record.success:
        return (
            f'System `{record.system}`: canary probe failed. '
            'The run was refused. Inspect its canary records.'
        )

    baseline = next(
        (
            old
            for old in previous
            if old.success
            and old.source_generate_fingerprint == record.source_generate_fingerprint
            and old.probe_protocol_digest == record.probe_protocol_digest
        ),
        None,
    )
    if baseline is None:
        return None
    assert baseline.usage is not None and record.usage is not None
    old_tokens, new_tokens = baseline.usage.input_tokens, record.usage.input_tokens
    models_changed = set(baseline.response_models) != set(record.response_models)
    if abs(old_tokens - new_tokens) <= max(2, old_tokens * 0.02) and not models_changed:
        return None
    model_change = 'The reported model changed. ' if models_changed else ''
    return (
        f'System `{record.system}`: canary differs from its baseline on '
        f'`{baseline.time.date()}`. Input tokens: `{old_tokens}` → `{new_tokens}`. '
        f'{model_change}The run was refused. Inspect its canary records. '
        'Give a changed system a new name. After verifying a benign route change, '
        f'run `bench reset-canary {record.system}` to clear its baseline contexts '
        'while retaining its probe and spend history.'
    )
