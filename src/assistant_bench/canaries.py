"""Single-request route canaries, their baselines, and independent spend records."""

import hashlib
import json
import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files

from harbor.models.job.config import JobConfig
from harbor.models.trial.result import TrialResult
from pydantic import BaseModel, ConfigDict

from assistant_bench import jobs
from assistant_bench.catalog import System, SystemIdentity
from assistant_bench.harnesses import fingerprint
from assistant_bench.harnesses.generate import OUTPUT_FILE
from assistant_bench.harnesses.usage import MeasuredUsage
from assistant_bench.messages import InputError
from assistant_bench.paths import data_dir
from assistant_bench.spend import update_canary_ledger
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


def prepare(configs: Iterable[JobConfig], systems: Mapping[str, System]) -> list[Probe]:
    """Prepare one probe for each selected system with at least one pending trial."""
    pending = {name for config in configs for name in jobs.pending_trials(config)}
    if not pending:
        return []

    root = data_dir() / 'canaries'
    task = root / 'task'
    task.mkdir(parents=True, exist_ok=True)
    instruction = files('assistant_bench').joinpath('canary.md').read_text()
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
        stable_config = config.model_dump(
            mode='json',
            include={
                'agents',
                'environment',
                'verifier',
                'retry',
                'n_attempts',
                'n_concurrent_trials',
                'timeout_multiplier',
            },
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


def run(probes: list[Probe]) -> list[Record]:
    """Record every outcome and its spend before refusing a failed or changed probe."""
    records: list[Record] = []
    problems: list[str] = []
    for probe in probes:
        previous = read_records(probe.original.name)
        run_error: str | None = 'InterruptedProbe'
        try:
            jobs.run_job(
                probe.config,
                log=probe.config.jobs_dir / f'{probe.config.job_name}.log',
            )
            run_error = None
        except subprocess.CalledProcessError as error:
            run_error = type(error).__name__
        finally:
            outcomes = _outcomes(probe, run_error)
            _append(outcomes)
            update_canary_ledger(
                [jobs.job_path(probe.config)], {probe.actual.name: probe.actual}
            )

        records.extend(outcomes)
        problems.extend(
            problem
            for record in outcomes
            if (problem := _problem(record, previous)) is not None
        )
    if problems:
        raise InputError('\n'.join(problems))
    return records


def read_records(system: str) -> list[Record]:
    path = data_dir() / 'canaries' / f'{system}.jsonl'
    return (
        [Record.model_validate_json(line) for line in path.read_text().splitlines()]
        if path.exists()
        else []
    )


def _outcomes(probe: Probe, run_error: str | None) -> list[Record]:
    directory = jobs.job_path(probe.config)
    results = [
        TrialResult.model_validate_json(path.read_text())
        for path in sorted(directory.glob('*/result.json'))
    ]
    return (
        [_record(probe, result, run_error) for result in results]
        if results
        else [_record(probe, None, run_error or 'MissingTrialResult')]
    )


def _record(probe: Probe, result: TrialResult | None, run_error: str | None) -> Record:
    directory = jobs.job_path(probe.config)
    context = result.agent_result if result else None
    metadata = (context.metadata or {}) if context else {}
    trial = result.trial_name if result else None
    output = directory / trial / 'agent' / OUTPUT_FILE if trial else None
    exception = result.exception_info if result else None
    model = metadata.get('response_model')
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
        usage=MeasuredUsage.from_details(metadata),
        response_models=[model] if isinstance(model, str) and model else [],
        output=output.read_text() if output and output.exists() else '',
        finish_reason=metadata.get('finish_reason'),
        latency_sec=metadata.get('latency_sec'),
        error=exception.exception_type if exception else run_error,
    )


def _append(records: list[Record]) -> None:
    for record in records:
        path = data_dir() / 'canaries' / f'{record.system}.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a') as file:
            file.write(record.model_dump_json() + '\n')


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
        'If the route changed models, delete the stale trials '
        "and the system's canary file. For a benign template change, delete only "
        'its canary file, then rerun.'
    )
