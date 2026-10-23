"""Harbor task rendering, job configs, runs, and ingest of trials."""

import hashlib
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
from harbor.models.task.verifier_mode import resolve_task_verifier_mode
from harbor.models.trial import config as trial_config
from harbor.models.trial.result import TrialResult
from harbor.trial.network_policy import resolve_trial_network_plan
from pydantic import ValidationError

from assistant_bench.catalog import System
from assistant_bench.families import (
    Case,
    Family,
    Gate,
    Settings,
    TaskTestsSettings,
    VotesSettings,
    case_task,
    error_lines,
    task_location,
    tasks_dir,
)
from assistant_bench.harnesses import HARNESSES
from assistant_bench.harnesses.generate import OUTPUT_FILE
from assistant_bench.outputs import Output, write_outputs
from assistant_bench.paths import data_dir
from assistant_bench.scoring.gates import failed_gates


STOCK_IMAGE = 'debian:trixie-slim'
# Ingest matches each trial's version exactly, while Harbor reuses a trial whose
# patch differs, so this base stays fixed.
TASK_VERSION = '1.0.0'
AGENT_TIMEOUT_SEC = 900.0

N_CONCURRENT_TRIALS = 8
# Harbor's reuse key covers the whole agent config, so changing this reruns every
# trial, as does renaming a system.
ROUTE_CONCURRENCY = 4
CHECK_ATTEMPTS = 3


# ── Jobs ──


def render_task_config(name: str, instruction: str) -> task_config.TaskConfig:
    """Return the Harbor task config that `render_tasks` writes for a task.

    The version's build metadata hashes the instruction and the settings set here,
    so a Harbor upgrade that changes a task.toml default keeps every version.
    """
    package = task_config.PackageInfo(name=name)
    config = task_config.TaskConfig(
        task=package,
        agent=task_config.AgentConfig(timeout_sec=AGENT_TIMEOUT_SEC),
        environment=task_config.EnvironmentConfig(docker_image=STOCK_IMAGE),
    )
    settings = config.model_dump_json(exclude_unset=True)
    digest = hashlib.sha256(f'{instruction}\0{settings}'.encode()).hexdigest()
    version = f'{TASK_VERSION}+{digest[:12]}'
    return config.model_copy(
        update={'task': package.model_copy(update={'version': version})}
    )


def render_tasks(family: Family, cases: list[Case]) -> list[Path]:
    """Write one Harbor task per case under the data directory and return their paths.

    Each task names a stock image that idles while the agent calls the endpoint from
    the host. It has no tests, which Harbor accepts while the verifier is disabled.
    A task's version follows its content. Directories of cases the family no longer
    has are removed.
    """
    directory = tasks_dir(family)
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for case in cases:
        installed = case_task(family, case.id)
        if installed.exists():
            shutil.rmtree(installed)
        paths.append(_render_task(family, case, installed))
    rendered = {path.name for path in paths}
    for installed in directory.iterdir():
        if installed.name in rendered:
            continue
        if installed.is_dir():
            shutil.rmtree(installed)
        else:
            installed.unlink()
    return paths


def _render_task(family: Family, case: Case, task_dir: Path) -> Path:
    task_dir.mkdir(parents=True)
    (task_dir / 'instruction.md').write_text(case.instruction)
    config = render_task_config(f'{family.name}/{case.id}', case.instruction)
    (task_dir / 'task.toml').write_text(config.model_dump_toml())
    return task_dir


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


def _agent_config(family: Family, system: System) -> trial_config.AgentConfig:
    harness = HARNESSES[system.harness]
    return trial_config.AgentConfig(
        import_path=harness.import_path,
        model_name=f'{harness.model_prefix}{system.route}',
        n_concurrent=ROUTE_CONCURRENCY,
        concurrency_group=system.route,
        kwargs=harness.options(system, family.settings),
    )


def case_job(family: Family, systems: list[System], tasks: list[Path]) -> JobConfig:
    """Build a job that runs every system on every case task.

    The job diffs against the family's earlier jobs, so Harbor runs only the trials
    that none of them completed with the same task version and agent config. A case
    family is scored by votes, so its verifier stays disabled.
    """
    agents = [_agent_config(family, system) for system in systems]
    sources = [source for source, _ in _sources(family)]
    return _job(
        family,
        data_dir() / 'jobs' / family.name,
        agents,
        tasks,
        sources,
        n_attempts=family.settings.samples,
        verify=False,
    )


def check_tasks(family: Family, tasks: list[Path]) -> None:
    """Check that every task sets what a run needs.

    Raises
    ------
    ValueError
        A task's `task.toml` does not parse, or it leaves a required setting at
        its default, naming the task and the value to set.
    """
    problems = []
    for task in tasks:
        location = f'{task_location(family, task)}/task.toml'
        try:
            config = _task_config(task)
        except ValidationError as error:
            problems.append(error_lines(error, location))
            continue
        problems.extend(_task_problems(config, location))
    if problems:
        raise ValueError(f'family {family.name!r}:\n' + '\n'.join(problems))


def _task_problems(config: task_config.TaskConfig, location: str) -> list[str]:
    problems = []
    if config.task is None or config.task.version is None:
        problems.append(f'{location}: set [task] version')
    if (
        config.verifier.environment_mode
        is not task_config.VerifierEnvironmentMode.SEPARATE
    ):
        problems.append(f'{location}: set [verifier] environment_mode = "separate"')
    if config.verifier.environment is not None:
        problems.append(
            f'{location}: do not set [verifier.environment], since the verifier '
            "uses the task's environment"
        )
    if config.steps:
        problems.append(
            f'{location}: do not set [[steps]], whose overrides the check cannot prove'
        )
    if config.verifier.environment is None and not config.steps:
        plan = resolve_trial_network_plan(
            config,
            trial_config.AgentConfig(),
            trial_config.EnvironmentConfig(),
            None,
            verifier_mode=resolve_task_verifier_mode(config),
        )
        for phase, policy in (
            ('agent', plan.agent_phase),
            ('verifier', plan.verifier_phase),
        ):
            if policy.network_mode is not task_config.NetworkMode.NO_NETWORK:
                problems.append(f'{location}: the {phase} phase needs no-network')
    if not any(
        (artifact if isinstance(artifact, str) else artifact.source) == '/app'
        for artifact in config.artifacts
    ):
        problems.append(f'{location}: list "/app" under [[artifacts]]')
    for field in ('cpus', 'memory_mb'):
        if getattr(config.environment, field) is None:
            problems.append(f'{location}: set [environment] {field}')
    if config.agent.timeout_sec is None:
        problems.append(f'{location}: set [agent] timeout_sec')
    return problems


def check_job(family: Family, tasks: list[Path]) -> JobConfig:
    """Build a job that runs the oracle and the nop on every task of the family.

    The oracle must pass every task while the untouched repository must fail it.
    Each agent runs `CHECK_ATTEMPTS` times on each task, which catches a task that
    passes or fails only sometimes. Its trials go under `checks/`, apart from the
    jobs that ingest reads.
    """
    return _job(
        family,
        data_dir() / 'checks' / family.name,
        [trial_config.AgentConfig(name=agent) for agent in ('oracle', 'nop')],
        tasks,
        [],
        n_attempts=CHECK_ATTEMPTS,
        verify=True,
    )


def has_jobs(family: Family) -> bool:
    """Return whether the family has at least one Harbor job."""
    return bool(_family_jobs(family))


def _family_jobs(family: Family) -> list[Path]:
    jobs_dir = data_dir() / 'jobs' / family.name
    return sorted(result.parent for result in jobs_dir.glob('*/result.json'))


def _sources(family: Family) -> list[tuple[SourceJobConfig, Path]]:
    return [_source(job_dir) for job_dir in _family_jobs(family)]


def _source(job_dir: Path) -> tuple[SourceJobConfig, Path]:
    return SourceJobConfig(action='diff', type='local', path=job_dir), job_dir


def _job(
    family: Family,
    jobs_dir: Path,
    agents: list[trial_config.AgentConfig],
    tasks: list[Path],
    sources: list[SourceJobConfig],
    *,
    n_attempts: int,
    verify: bool,
) -> JobConfig:
    return JobConfig(
        jobs_dir=jobs_dir,
        n_attempts=n_attempts,
        n_concurrent_trials=N_CONCURRENT_TRIALS,
        quiet=True,
        retry=RetryConfig(max_retries=1),
        verifier=trial_config.VerifierConfig(disable=not verify),
        agents=agents,
        tasks=[trial_config.TaskConfig(path=path) for path in tasks],
        source_jobs=sources,
    )


def job_path(config: JobConfig) -> Path:
    """Return the directory Harbor writes the job to."""
    return config.jobs_dir / config.job_name


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
            # LiteLLM loads a .env above its own install unless its mode is
            # PRODUCTION, and bench has already loaded the checkout's.
            env={**os.environ, 'HARBOR_TELEMETRY': 'off', 'LITELLM_MODE': 'PRODUCTION'},
        )
    return job_path(config)


# ── Ingest ──


def ingest(family: Family, systems: dict[str, System]) -> list[Output]:
    """Read the family's trials into `outputs/<family>.jsonl`.

    A case family keeps only trials of each case's installed task. Each case, system,
    and fingerprint keeps at most `samples` outputs, preferring a successful trial
    whose recorded request matches the family's current settings, and otherwise the
    oldest. Costs come from the current catalog price, so a system missing from
    `systems` or without a price reports as unpriced.

    Raises
    ------
    ValueError
        A source job is not a job directory, or a trial record is unreadable.
    """
    expected = (
        {
            name: HARNESSES[system.harness].options(system, family.settings)
            for name, system in systems.items()
        }
        if family.source == 'cases'
        else {}
    )
    versions = _installed_versions(family) if family.source == 'cases' else None
    candidates = [
        (output, kwargs == expected.get(output.system))
        for output, kwargs in _read_trials(family, _sources(family), systems, versions)
    ]
    candidates.sort(
        key=lambda candidate: (
            candidate[0].error is not None,
            not candidate[1],
            candidate[0].job,
            candidate[0].trial,
        )
    )
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
    """Return one output per finished trial of a job.

    Raises
    ------
    ValueError
        The directory is not a job directory, or a trial record is unreadable.
    """
    return [output for output, _ in _read_trials(family, [_source(job_dir)], systems)]


def _read_trials(
    family: Family,
    sources: list[tuple[SourceJobConfig, Path]],
    systems: dict[str, System],
    versions: dict[str, str] | None = None,
) -> list[tuple[Output, dict[str, Any]]]:
    """Return each kept trial's output and the agent kwargs it recorded.

    A `versions` mapping drops a trial whose task is not the case's installed task
    before an output reads that task.
    """
    records = []
    for trial in load_source_trials(sources):
        case = trial.result.task_name.removeprefix(f'{family.name}/')
        if versions is not None and trial.lock.task.version != versions.get(case):
            continue
        output = _output(family, trial.trial_dir, trial.result, case, systems)
        records.append((output, trial.result.config.agent.kwargs))
    return records


def _installed_versions(family: Family) -> dict[str, str]:
    """Return each installed task's version, keyed by the case ID."""
    directory = tasks_dir(family)
    if not directory.is_dir():
        return {}
    versions = {}
    for task in directory.iterdir():
        if not (task / 'task.toml').is_file():
            continue
        package = _task_config(task).task
        if package is not None and package.version is not None:
            versions[task.name] = package.version
    return versions


def _task_config(task_dir: Path) -> task_config.TaskConfig:
    return task_config.TaskConfig.model_validate_toml(
        (task_dir / 'task.toml').read_text()
    )


def _output(
    family: Family,
    trial_dir: Path,
    result: TrialResult,
    case: str,
    systems: dict[str, System],
) -> Output:
    kwargs = result.config.agent.kwargs
    system = kwargs.get('system', result.agent_info.name)
    context = result.agent_result or AgentContext()
    metadata = context.metadata or {}
    exception = result.exception_info
    output_file = trial_dir / 'agent' / OUTPUT_FILE
    text = output_file.read_text() if output_file.exists() else None
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
