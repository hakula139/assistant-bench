"""Harbor task rendering, job configs, runs, and collection of trials."""

import hashlib
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, assert_never
from uuid import UUID

import semver
from harbor.job_diff import SourceTrial, load_source_trials
from harbor.models.agent.context import AgentContext
from harbor.models.job.config import JobConfig, RetryConfig, SourceJobConfig
from harbor.models.task import config as task_config
from harbor.models.task.artifacts import source_host_path
from harbor.models.task.verifier_mode import resolve_task_verifier_mode
from harbor.models.trial import config as trial_config
from harbor.models.trial.paths import TrialPaths
from harbor.models.trial.result import TrialResult
from harbor.publisher.packager import Packager
from harbor.trial.network_policy import resolve_trial_network_plan
from pydantic import ValidationError

from assistant_bench.catalog import System
from assistant_bench.diffs import workspace_diff
from assistant_bench.families import (
    Case,
    Family,
    Gate,
    TaskTestsSettings,
    VotesSettings,
    case_task,
    load_cases,
    task_location,
    tasks_dir,
)
from assistant_bench.harnesses import HARNESSES
from assistant_bench.harnesses.generate import OUTPUT_FILE
from assistant_bench.messages import code, error_lines
from assistant_bench.outputs import Output, write_outputs
from assistant_bench.paths import data_dir
from assistant_bench.scoring.gates import failed_gates


STOCK_IMAGE = 'debian:trixie-slim'
# Collection matches each trial's version exactly, while Harbor reuses a trial whose
# patch differs, so this base stays fixed.
TASK_VERSION = '1.0.0'
AGENT_TIMEOUT_SEC = 900.0
WORKSPACE = '/app'

N_CONCURRENT_TRIALS = 8
# Caps task trials, each of which can hold an agent and a verifier container.
N_CONCURRENT_CONTAINERS = 4
# Harbor's reuse key covers the whole agent config, so changing this reruns every
# trial, as does renaming a system.
ROUTE_CONCURRENCY = 4
CHECK_ATTEMPTS = 3
# LiteLLM loads a .env above its own install unless its mode is PRODUCTION, and
# bench loads the working directory's .env itself.
HARBOR_ENV = {'HARBOR_TELEMETRY': 'off', 'LITELLM_MODE': 'PRODUCTION'}


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


def family_systems(family: Family, systems: list[System]) -> list[System]:
    """Return the systems as the family runs them.

    A task family runs every system under its `harness`, which enters the
    system's fingerprint, while a case family runs each system as it resolves.

    Raises
    ------
    ValueError
        The family names an unknown harness, or a system has a prompt that the
        harness cannot send.
    """
    harness = family.settings.harness
    if harness is None:
        return systems
    if harness not in HARNESSES:
        raise ValueError(
            f'Family `{family.name}`: harness `{harness}` is not one of '
            f'{code(HARNESSES)}.'
        )
    if not HARNESSES[harness].system_prompt:
        prompted = [system.name for system in systems if system.prompt is not None]
        if prompted:
            raise ValueError(
                f'Family `{family.name}` lists {code(prompted)}, whose prompt the '
                f'`{harness}` harness cannot send. Remove them from `systems`.'
            )
    return [system.model_copy(update={'harness': harness}) for system in systems]


def drifted_systems(
    family: Family, systems: list[System]
) -> list[tuple[str, str, str]]:
    """Return each selected system whose earlier trials recorded another fingerprint.

    The systems are as `family_systems` returns them. An entry names the system,
    the fingerprint an earlier trial recorded, and the fingerprint the system
    resolves to now. A system can contribute one entry per recorded fingerprint,
    and the list is sorted by system name.

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


def job(family: Family, systems: list[System], tasks: list[Path]) -> JobConfig:
    """Build a job that runs every system on every task of the family.

    The systems are as `family_systems` returns them. The job diffs against the
    family's earlier jobs, so Harbor runs only the trials that none of them
    completed with the same task version and agent config. A task's tests grade
    its trials whatever the scorer, while a rendered case has no tests, so the
    verifier runs only for a task family, which also runs fewer trials at once.
    """
    agents = [_agent_config(family, system) for system in systems]
    sources = [source for source, _ in _sources(family)]
    tasks_source = family.source == 'tasks'
    return _job(
        family,
        data_dir() / 'jobs' / family.name,
        agents,
        tasks,
        sources,
        n_attempts=family.settings.samples,
        n_concurrent=N_CONCURRENT_CONTAINERS if tasks_source else N_CONCURRENT_TRIALS,
        verify=tasks_source,
    )


def changed_tasks(family: Family, tasks: list[Path]) -> list[tuple[str, str]]:
    """Return each task whose content changed since a trial recorded its version.

    Harbor aborts a job whose task has an earlier trial at the same version but
    another digest. An entry names the task and that version, sorted by task.

    Raises
    ------
    ValueError
        A source job is not a job directory, or a trial record is unreadable.
    """
    current: dict[str, tuple[str | None, str]] = {}
    for task in tasks:
        content_hash, _ = Packager.compute_content_hash(task)
        current[f'{family.name}/{task.name}'] = (
            _task_version(task),
            f'sha256:{content_hash}',
        )
    changed = set()
    for trial in load_source_trials(_sources(family)):
        if trial.task_name not in current:
            continue
        version, digest = current[trial.task_name]
        if (
            version is not None
            and trial.lock.task.version == version
            and trial.lock.task.digest != digest
        ):
            changed.add((trial.task_name.removeprefix(f'{family.name}/'), version))
    return sorted(changed)


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
        location = f'`{task_location(family, task)}/task.toml`'
        try:
            config = _task_config(task)
        except ValidationError as error:
            problems.append(error_lines(error, location))
            continue
        problems.extend(_task_problems(config, location))
    if problems:
        raise ValueError(
            f'Family `{family.name}` has tasks to fix:\n' + '\n'.join(problems)
        )


def _task_problems(config: task_config.TaskConfig, location: str) -> list[str]:
    problems = []
    if config.task is None or config.task.version is None:
        problems.append(f'{location}: set `[task] version`.')
    if (
        config.verifier.environment_mode
        is not task_config.VerifierEnvironmentMode.SEPARATE
    ):
        problems.append(f'{location}: set `[verifier] environment_mode = "separate"`.')
    if config.verifier.environment is not None:
        problems.append(
            f'{location}: remove `[verifier.environment]`, since the verifier '
            "uses the task's environment."
        )
    if config.steps:
        problems.append(
            f'{location}: remove `[[steps]]`, whose overrides the check cannot prove.'
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
                problems.append(f'{location}: the {phase} phase needs `no-network`.')
    if not any(
        (artifact if isinstance(artifact, str) else artifact.source) == WORKSPACE
        for artifact in config.artifacts
    ):
        problems.append(f'{location}: list `"{WORKSPACE}"` under `[[artifacts]]`.')
    for field in ('cpus', 'memory_mb'):
        if getattr(config.environment, field) is None:
            problems.append(f'{location}: set `[environment] {field}`.')
    if config.agent.timeout_sec is None:
        problems.append(f'{location}: set `[agent] timeout_sec`.')
    return problems


def check_job(family: Family, tasks: list[Path]) -> JobConfig:
    """Build a job that runs the oracle and the nop on every task of the family.

    The oracle must pass every task while the untouched repository must fail it.
    Each agent runs `CHECK_ATTEMPTS` times on each task, which catches a task that
    passes or fails only sometimes. Its trials go under `checks/`, apart from the
    jobs that `collect` reads.
    """
    return _job(
        family,
        data_dir() / 'checks' / family.name,
        [trial_config.AgentConfig(name=agent) for agent in ('oracle', 'nop')],
        tasks,
        [],
        n_attempts=CHECK_ATTEMPTS,
        n_concurrent=N_CONCURRENT_CONTAINERS,
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
    n_concurrent: int,
    verify: bool,
) -> JobConfig:
    return JobConfig(
        jobs_dir=jobs_dir,
        n_attempts=n_attempts,
        n_concurrent_trials=n_concurrent,
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

    Harbor inherits the process environment, which must hold `HARBOR_ENV`.

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
        subprocess.run([harbor, 'run', '--config', file.name, '--yes'], check=True)
    return job_path(config)


# ── Collection ──


def collect(family: Family, systems: dict[str, System]) -> list[Output]:
    """Read the family's trials into `outputs/<family>.jsonl`.

    A case family keeps only trials of each case's installed task. A task family
    keeps only trials of its current tasks whose version matches the task's in
    major and minor, so a patch bump keeps the trials Harbor reused, while a minor
    bump keeps only their regrades. Each case, system, and fingerprint keeps at
    most `samples` outputs, preferring a successful trial whose recorded request
    matches what the family sends a listed system now, and otherwise the oldest.
    Costs come from the current catalog price, so a system missing from `systems`
    or without a price reports as unpriced.

    A regraded trial yields the output of the trial whose agent wrote its
    solution, with the regrade's verdict, so the votes on that output stay.

    Raises
    ------
    ValueError
        A source job is not a job directory, a trial record is unreadable, a
        task is invalid, or the family's systems are, as `family_systems` says.
    """
    listed = [systems[name] for name in family.settings.systems if name in systems]
    expected = {
        system.name: HARNESSES[system.harness].options(system, family.settings)
        for system in family_systems(family, listed)
    }
    candidates = [
        (output, kwargs == expected.get(output.system))
        for output, kwargs in _read_trials(
            family, _sources(family), systems, _current_versions(family)
        )
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

    A `versions` mapping drops a trial whose task version is not current before
    an output reads that task.
    """
    trials = load_source_trials(sources)
    by_id = {trial.id: trial for trial in trials}
    records = []
    for trial in trials:
        case = trial.task_name.removeprefix(f'{family.name}/')
        if versions is not None and not _is_current(
            family, trial.lock.task.version, versions.get(case)
        ):
            continue
        output = _output(family, trial, _origin(trial, by_id), case, systems)
        records.append((output, trial.result.config.agent.kwargs))
    return records


def _current_versions(family: Family) -> dict[str, str]:
    """Return the version of each current task, keyed by the case ID."""
    match family.source:
        case 'cases':
            directory = tasks_dir(family)
            tasks = list(directory.iterdir()) if directory.is_dir() else []
        case 'tasks':
            tasks = [case_task(family, case.id) for case in load_cases(family)]
        case _:
            assert_never(family.source)
    return {
        task.name: version
        for task in tasks
        if (task / 'task.toml').is_file() and (version := _task_version(task))
    }


def _is_current(family: Family, version: str | None, current: str | None) -> bool:
    """Return whether a trial's task version matches its task's current version.

    A rendered case matches exactly, since its build metadata hashes its content,
    while a task matches in major and minor, the levels Harbor reruns or regrades.
    """
    if version is None or current is None:
        return False
    if family.source == 'cases':
        return version == current
    recorded = semver.Version.parse(version)
    installed = semver.Version.parse(current)
    return (recorded.major, recorded.minor) == (installed.major, installed.minor)


def _origin(trial: SourceTrial, trials: dict[UUID, SourceTrial]) -> SourceTrial:
    """Return the trial whose agent wrote the trial's solution.

    A regrade names its source trial, which may be a regrade itself. A source
    outside `trials` ends the chain, since the regrade holds a copy of its record.
    """
    while (source := trial.result.config.source_trial) is not None and (
        source.trial_id in trials
    ):
        trial = trials[source.trial_id]
    return trial


def _task_version(task_dir: Path) -> str | None:
    package = _task_config(task_dir).task
    return package.version if package is not None else None


def _task_config(task_dir: Path) -> task_config.TaskConfig:
    return task_config.TaskConfig.model_validate_toml(
        (task_dir / 'task.toml').read_text()
    )


def _output(
    family: Family,
    trial: SourceTrial,
    origin: SourceTrial,
    case: str,
    systems: dict[str, System],
) -> Output:
    """Return the output that `origin` wrote, with the verdict `trial` recorded."""
    result = origin.result
    kwargs = result.config.agent.kwargs
    system = kwargs.get('system', result.agent_info.name)
    context = result.agent_result or AgentContext()
    metadata = context.metadata or {}
    exception = trial.result.exception_info
    reward = _reward(trial.result) if family.source == 'tasks' else None
    text = _text(family, origin, case, reward)
    finish_reason = metadata.get('finish_reason')

    return Output(
        trial=result.trial_name,
        job=origin.trial_dir.parent.name,
        case=case,
        system=system,
        fingerprint=kwargs.get('fingerprint'),
        nixos_config_rev=metadata.get('nixos_config_rev'),
        harness_version=metadata.get('harbor_version'),
        error=exception.exception_type if exception else None,
        text=text,
        reward=reward,
        gate_failures=_gate_failures(
            family, case, text, finish_reason, reward, errored=exception is not None
        ),
        finish_reason=finish_reason,
        input_tokens=context.n_input_tokens,
        cached_tokens=context.n_cache_tokens,
        output_tokens=context.n_output_tokens,
        reasoning_tokens=metadata.get('reasoning_tokens'),
        cost_usd=_cost(systems.get(system), context),
        latency_sec=_latency(result, metadata),
        response_model=metadata.get('response_model'),
        request_id=metadata.get('request_id'),
    )


def _reward(result: TrialResult) -> float | None:
    rewards = result.verifier_result and result.verifier_result.rewards
    return rewards.get('reward') if rewards else None


def _text(
    family: Family, origin: SourceTrial, case: str, reward: float | None
) -> str | None:
    """Return a case's reply, or the diff of a graded solution on a voted task."""
    match family.source:
        case 'cases':
            output_file = origin.trial_dir / 'agent' / OUTPUT_FILE
            return output_file.read_text() if output_file.exists() else None
        case 'tasks':
            if not isinstance(family.settings, VotesSettings) or reward is None:
                return None
            artifacts = TrialPaths(origin.trial_dir).artifacts_dir
            return workspace_diff(
                case_task(family, case) / 'environment' / 'app',
                source_host_path(artifacts, WORKSPACE),
            )
        case _:
            assert_never(family.source)


def _gate_failures(
    family: Family,
    case: str,
    text: str | None,
    finish_reason: str | None,
    reward: float | None,
    *,
    errored: bool,
) -> list[Gate] | None:
    """Return the gates a voted output fails, or `None` when it has no verdict.

    A task's tests give the verdict even when its agent errored, as on a timeout,
    since Harbor still runs the verifier.
    """
    match family.settings:
        case VotesSettings(gates=gates):
            if family.source == 'tasks':
                if reward is None:
                    return None
                return [Gate.TESTS] if reward < 1 else []
            if errored:
                return None
            instruction = (
                (case_task(family, case) / 'instruction.md').read_text()
                if gates.script
                else ''
            )
            return failed_gates(family.settings, text or '', finish_reason, instruction)
        case TaskTestsSettings():
            return None
        case _:
            assert_never(family.settings)


def _latency(result: TrialResult, metadata: dict[str, Any]) -> float | None:
    """Return the latency the agent recorded, or else its execution time."""
    latency: float | None = metadata.get('latency_sec')
    timing = result.agent_execution
    if latency is not None or timing is None:
        return latency
    if timing.started_at is None or timing.finished_at is None:
        return None
    return round((timing.finished_at - timing.started_at).total_seconds(), 3)


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
