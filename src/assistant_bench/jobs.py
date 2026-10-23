"""Harbor task rendering, job configs, runs, and ingest of trials."""

import os
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, assert_never

from harbor.job_diff import load_source_trials
from harbor.models.agent.context import AgentContext
from harbor.models.job.config import JobConfig, RetryConfig, SourceJobConfig
from harbor.models.task import config as task_config
from harbor.models.trial import config as trial_config
from harbor.models.trial.result import TrialResult
from harbor.publisher.packager import Packager

from assistant_bench.catalog import System
from assistant_bench.families import (
    Case,
    Family,
    Gate,
    Settings,
    TaskTestsSettings,
    VotesSettings,
    case_task,
)
from assistant_bench.harnesses import HARNESSES
from assistant_bench.harnesses.generate import OUTPUT_FILE, GenerateOptions
from assistant_bench.outputs import Output, write_outputs
from assistant_bench.paths import FAMILIES_DIR, data_dir
from assistant_bench.scoring.gates import failed_gates


STOCK_IMAGE = 'debian:trixie-slim'
# Harbor reuses a trial whose task version matches up to the patch level, and aborts
# the job when a task keeps its version but changes its content. An edited case
# therefore takes a new ID, and a change to every rendered task bumps the patch.
TASK_VERSION = '1.0.0'
AGENT_TIMEOUT_SEC = 900.0
N_CONCURRENT_TRIALS = 8
# Harbor's reuse key covers the whole agent config, so changing this reruns every
# trial, as does renaming a system.
ROUTE_CONCURRENCY = 4


# ── Jobs ──


def render_tasks(family: Family, cases: list[Case]) -> list[Path]:
    """Write one Harbor task per case under the data directory and return their paths.

    Each task names a stock image that idles while the agent calls the endpoint from
    the host. It has no tests, which Harbor accepts while the verifier is disabled.
    The tasks render into a staging directory and replace the installed ones only
    when none differs from what an earlier trial ran, so a rejected edit leaves each
    task as its trials ran it.

    Raises
    ------
    ValueError
        A case's task differs from the one an earlier trial ran.
    """
    data_dir().mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=data_dir()) as staging:
        staged = [_render_task(family, case, Path(staging) / case.id) for case in cases]
        match _changed_tasks(family, staged):
            case []:
                pass
            case [case_id]:
                raise ValueError(
                    f'family {family.name!r}: case {case_id!r} changed, '
                    'give it a new ID'
                )
            case changed:
                ids = ', '.join(repr(case_id) for case_id in changed)
                raise ValueError(
                    f'family {family.name!r}: cases {ids} changed, give each a new ID'
                )

        paths = []
        for task in staged:
            installed = case_task(family, task.name)
            if installed.exists():
                shutil.rmtree(installed)
            installed.parent.mkdir(parents=True, exist_ok=True)
            paths.append(task.rename(installed))
        return paths


def _render_task(family: Family, case: Case, task_dir: Path) -> Path:
    task_dir.mkdir(parents=True)
    (task_dir / 'instruction.md').write_text(case.instruction)
    config = task_config.TaskConfig(
        task=task_config.PackageInfo(
            name=f'{family.name}/{case.id}', version=TASK_VERSION
        ),
        agent=task_config.AgentConfig(timeout_sec=AGENT_TIMEOUT_SEC),
        environment=task_config.EnvironmentConfig(docker_image=STOCK_IMAGE),
    )
    (task_dir / 'task.toml').write_text(config.model_dump_toml())
    return task_dir


def _changed_tasks(family: Family, tasks: list[Path]) -> list[str]:
    """Return the IDs of rendered tasks whose content differs from what a trial ran.

    Harbor records each trial's task digest, and aborts a job whose task keeps its
    version but no longer matches that digest.
    """
    recorded: defaultdict[str, set[str]] = defaultdict(set)
    for trial in load_source_trials(_sources(family)):
        if trial.lock.task.version == TASK_VERSION:
            recorded[trial.task_name].add(trial.lock.task.digest)
    changed = []
    for task in tasks:
        digests = recorded.get(f'{family.name}/{task.name}', set())
        content_hash, _ = Packager.compute_content_hash(task)
        if digests - {f'sha256:{content_hash}'}:
            changed.append(task.name)
    return changed


def drifted_systems(
    family: Family, systems: list[System]
) -> list[tuple[str, str, str]]:
    """Return each selected system whose earlier trials recorded another fingerprint.

    An entry names the system, the fingerprint an earlier trial recorded, and the
    fingerprint the system resolves to now. A system can contribute one entry per
    recorded fingerprint, and the list is sorted by system name.

    Raises
    ------
    ValueError
        A source job is not a job directory, or a trial record is unreadable.
    """
    current = {system.name: system.fingerprint for system in systems}
    recorded: defaultdict[str, set[str]] = defaultdict(set)
    for trial in load_source_trials(_sources(family)):
        kwargs = trial.result.config.agent.kwargs
        name = kwargs.get('system')
        fingerprint = kwargs.get('fingerprint')
        if name in current and isinstance(fingerprint, str):
            recorded[name].add(fingerprint)
    return sorted(
        (name, fingerprint, current[name])
        for name, fingerprints in recorded.items()
        for fingerprint in fingerprints
        if fingerprint != current[name]
    )


def _request_kwargs(family: Family, system: System) -> dict[str, Any]:
    """Return the agent kwargs that carry a system's request to the endpoint."""
    assert family.settings.max_output_tokens is not None
    return GenerateOptions(
        system=system.name,
        fingerprint=system.fingerprint,
        effort=system.effort,
        system_prompt=system.prompt,
        max_output_tokens=family.settings.max_output_tokens,
    ).model_dump()


def case_job(family: Family, systems: list[System], tasks: list[Path]) -> JobConfig:
    """Build a job that runs every system on every case task.

    The job diffs against the family's earlier jobs, so Harbor runs only the trials
    that none of them completed with the same task version and agent config.
    """
    agents = [
        trial_config.AgentConfig(
            name=HARNESSES[system.harness].name,
            import_path=HARNESSES[system.harness].import_path,
            model_name=system.route,
            n_concurrent=ROUTE_CONCURRENCY,
            concurrency_group=system.route,
            kwargs=_request_kwargs(family, system),
        )
        for system in systems
    ]
    sources = [source for source, _ in _sources(family)]
    return _job(family, data_dir() / 'jobs' / family.name, agents, tasks, sources)


def oracle_job(family: Family) -> JobConfig:
    """Build a job that runs Harbor's oracle on every task directory of the family.

    Its trials go under `checks/`, apart from the jobs that ingest reads.

    Raises
    ------
    ValueError
        A task's `[task] name` is not `<family>/<directory>`, which trials report.
    """
    tasks = sorted((FAMILIES_DIR / family.name / 'tasks').iterdir())
    misnamed = [
        f'"{family.name}/{task.name}" in tasks/{task.name}/task.toml'
        for task in tasks
        if _task_name(task) != f'{family.name}/{task.name}'
    ]
    if misnamed:
        raise ValueError(
            f'family {family.name!r}: set [task] name to {", ".join(misnamed)}'
        )
    return _job(
        family,
        data_dir() / 'checks' / family.name,
        [
            trial_config.AgentConfig(
                name=HARNESSES['oracle'].name,
                import_path=HARNESSES['oracle'].import_path,
            )
        ],
        tasks,
        [],
    )


def _task_name(task_dir: Path) -> str | None:
    config = task_config.TaskConfig.model_validate_toml(
        (task_dir / 'task.toml').read_text()
    )
    return config.task.name if config.task else None


def has_jobs(family: Family) -> bool:
    """Return whether the family has at least one Harbor job."""
    return bool(_family_jobs(family))


def _family_jobs(family: Family) -> list[Path]:
    jobs_dir = data_dir() / 'jobs' / family.name
    return sorted(result.parent for result in jobs_dir.glob('*/result.json'))


def _sources(family: Family) -> list[tuple[SourceJobConfig, Path]]:
    return [
        (SourceJobConfig(action='diff', type='local', path=job_dir), job_dir)
        for job_dir in _family_jobs(family)
    ]


def _job(
    family: Family,
    jobs_dir: Path,
    agents: list[trial_config.AgentConfig],
    tasks: list[Path],
    sources: list[SourceJobConfig],
) -> JobConfig:
    return JobConfig(
        jobs_dir=jobs_dir,
        n_attempts=family.settings.samples,
        n_concurrent_trials=N_CONCURRENT_TRIALS,
        quiet=True,
        retry=RetryConfig(max_retries=1),
        verifier=trial_config.VerifierConfig(disable=not _runs_tests(family.settings)),
        agents=agents,
        tasks=[trial_config.TaskConfig(path=path) for path in tasks],
        source_jobs=sources,
    )


def _runs_tests(settings: Settings) -> bool:
    match settings:
        case VotesSettings():
            return False
        case TaskTestsSettings():
            return True
        case _:
            assert_never(settings)


def run_job(config: JobConfig) -> Path:
    """Run the job with the Harbor CLI and return the job directory.

    Raises
    ------
    subprocess.CalledProcessError
        Harbor exited with an error.
    """
    harbor = Path(sys.executable).with_name('harbor')
    with tempfile.NamedTemporaryFile(
        'w', suffix='.json', delete_on_close=False
    ) as file:
        file.write(config.model_dump_json())
        file.close()
        subprocess.run(
            [harbor, 'run', '--config', file.name, '--yes'],
            check=True,
            env={**os.environ, 'HARBOR_TELEMETRY': 'off'},
        )
    return config.jobs_dir / config.job_name


# ── Ingest ──


def ingest(family: Family, systems: dict[str, System]) -> list[Output]:
    """Read the family's trials into `outputs/<family>.jsonl`.

    Each case, system, and fingerprint keeps at most `samples` outputs, preferring
    a successful trial whose recorded request matches the family's current settings,
    and otherwise the oldest. Costs come from the current catalog price, so a system
    missing from `systems` or without a price reports as unpriced.
    """
    expected = (
        {name: _request_kwargs(family, system) for name, system in systems.items()}
        if family.source == 'cases'
        else {}
    )
    candidates = [
        (output, kwargs == expected.get(output.system))
        for job_dir in _family_jobs(family)
        for output, kwargs in _read_trials(family, job_dir, systems)
    ]
    candidates.sort(key=lambda candidate: candidate[0].trial)
    candidates.sort(key=lambda candidate: candidate[0].job)
    candidates.sort(key=lambda candidate: not candidate[1])
    candidates.sort(key=lambda candidate: candidate[0].error is not None)
    groups: defaultdict[tuple[str, str, str | None], list[Output]] = defaultdict(list)
    for output, _ in candidates:
        groups[(output.case, output.system, output.fingerprint)].append(output)
    outputs = sorted(
        (
            output
            for group in groups.values()
            for output in group[: family.settings.samples]
        ),
        key=lambda output: (output.case, output.system, output.job, output.trial),
    )

    write_outputs(family.name, outputs)
    return outputs


def read_trials(
    family: Family, job_dir: Path, systems: dict[str, System]
) -> list[Output]:
    """Return one output per finished trial of a job."""
    return [output for output, _ in _read_trials(family, job_dir, systems)]


def _read_trials(
    family: Family, job_dir: Path, systems: dict[str, System]
) -> list[tuple[Output, dict[str, Any]]]:
    """Return each finished trial's output and the agent kwargs it recorded."""
    records = []
    for result_path in sorted(job_dir.glob('*/result.json')):
        trial_dir = result_path.parent
        result = TrialResult.model_validate_json(result_path.read_bytes())
        records.append(
            (_output(family, trial_dir, result, systems), result.config.agent.kwargs)
        )
    return records


def _output(
    family: Family, trial_dir: Path, result: TrialResult, systems: dict[str, System]
) -> Output:
    kwargs = result.config.agent.kwargs
    system = kwargs.get('system', result.agent_info.name)
    context = result.agent_result or AgentContext()
    metadata = context.metadata or {}
    exception = result.exception_info
    output_file = trial_dir / 'agent' / OUTPUT_FILE
    text = output_file.read_text() if output_file.exists() else None
    case = result.task_name.removeprefix(f'{family.name}/')
    finish_reason = metadata.get('finish_reason')

    return Output(
        trial=result.trial_name,
        job=trial_dir.parent.name,
        case=case,
        system=system,
        fingerprint=kwargs.get('fingerprint'),
        nixos_config_rev=metadata.get('nixos_config_rev'),
        error=exception.exception_type if exception else None,
        text=text,
        reward=_reward(family.settings, result),
        gate_failures=None
        if exception
        else _gate_failures(family, case, text or '', finish_reason),
        finish_reason=finish_reason,
        input_tokens=context.n_input_tokens,
        cached_tokens=context.n_cache_tokens,
        output_tokens=context.n_output_tokens,
        reasoning_tokens=metadata.get('reasoning_tokens'),
        cost_usd=_cost(systems.get(system), context),
        latency_sec=metadata.get('latency_sec'),
        response_model=metadata.get('response_model'),
        request_id=metadata.get('request_id'),
    )


def _reward(settings: Settings, result: TrialResult) -> float | None:
    match settings:
        case VotesSettings():
            return None
        case TaskTestsSettings():
            rewards = result.verifier_result and result.verifier_result.rewards
            return rewards.get('reward') if rewards else None
        case _:
            assert_never(settings)


def _gate_failures(
    family: Family, case: str, text: str, finish_reason: str | None
) -> list[Gate] | None:
    match family.settings:
        case VotesSettings(gates=gates):
            instruction = (
                (case_task(family, case) / 'instruction.md').read_text()
                if gates.script
                else ''
            )
            return failed_gates(family.settings, text, finish_reason, instruction)
        case TaskTestsSettings():
            return None
        case _:
            assert_never(family.settings)


def _cost(system: System | None, context: AgentContext) -> float | None:
    if (
        system is None
        or system.price is None
        or context.n_input_tokens is None
        or context.n_output_tokens is None
    ):
        return None
    return system.price.cost(
        context.n_input_tokens, context.n_cache_tokens or 0, context.n_output_tokens
    )
