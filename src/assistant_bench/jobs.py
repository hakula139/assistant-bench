"""Harbor task rendering, job configs, runs, and collection of trials."""

import hashlib
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, assert_never
from uuid import UUID

import semver
from harbor.job_diff import SourceTrial, load_source_trials
from harbor.models.agent.context import AgentContext
from harbor.models.environment_type import EnvironmentType
from harbor.models.job.config import JobConfig, RetryConfig, SourceJobConfig
from harbor.models.task import config as task_config
from harbor.models.task.artifacts import source_host_path
from harbor.models.task.verifier_mode import resolve_task_verifier_mode
from harbor.models.trial import config as trial_config
from harbor.models.trial.paths import TrialPaths
from harbor.models.trial.result import TrialResult
from harbor.publisher.packager import Packager
from harbor.trial.network_policy import resolve_trial_network_plan
from pydantic import BaseModel, Field, ValidationError

from assistant_bench.catalog import SYSTEMS_FILE, NixosConfigExport, System
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
from assistant_bench.harnesses import (
    HARNESSES,
    HOST_ENVIRONMENT,
    agent_kwargs,
    fingerprint,
)
from assistant_bench.harnesses.generate import OUTPUT_FILE
from assistant_bench.harnesses.messages import MessageCompletionError
from assistant_bench.harnesses.responses import ResponseCompletionError
from assistant_bench.messages import code, error_lines
from assistant_bench.outputs import Output, tests_pass, write_outputs
from assistant_bench.paths import data_dir
from assistant_bench.scoring.gates import failed_gates


# Collection matches each trial's version exactly, while Harbor reuses a trial whose
# patch differs, so this base stays fixed.
TASK_VERSION = '1.0.0'
AGENT_TIMEOUT_SEC = 3600.0
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
    )
    settings = config.model_dump_json(exclude_unset=True)
    digest = hashlib.sha256(f'{instruction}\0{settings}'.encode()).hexdigest()
    version = f'{TASK_VERSION}+{digest[:12]}'
    return config.model_copy(
        update={'task': package.model_copy(update={'version': version})}
    )


def render_tasks(family: Family, cases: list[Case]) -> list[Path]:
    """Write one Harbor task per case under the data directory and return their paths.

    A task names no image, since its trial starts no container while the agent calls
    the endpoint from the host. It has no tests, which Harbor accepts while the
    verifier is disabled. A task's version follows its content. Directories of cases
    the family no longer has are removed.
    """
    directory = tasks_dir(family)
    directory.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
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


def check_harness(family: Family) -> None:
    """Check that a task family's harness can solve its tasks.

    Raises
    ------
    ValueError
        The family names an unknown harness or a single-call one.
    """
    harness = family.settings.harness
    if harness is None:
        return
    if harness not in HARNESSES:
        raise ValueError(
            f'Family `{family.name}`: harness `{harness}` is not one of '
            f'{code(HARNESSES)}.'
        )
    if HARNESSES[harness].single_call:
        agents = [name for name, entry in HARNESSES.items() if not entry.single_call]
        raise ValueError(
            f'Family `{family.name}`: harness `{harness}` makes one request and '
            f"never acts in a task's environment, so choose among {code(agents)}."
        )


def listed_systems(family: Family, systems: Mapping[str, System]) -> list[System]:
    """Return the systems a family lists, refusing a name `systems.toml` lacks."""
    unknown = [name for name in family.settings.systems if name not in systems]
    if unknown:
        raise ValueError(
            f'Family `{family.name}` lists {code(unknown)}, which '
            f'`{SYSTEMS_FILE}` does not define. Fix '
            f'`families/{family.name}/family.toml` or add the system table.'
        )
    return [systems[name] for name in family.settings.systems]


def check_systems(family: Family, systems: list[System]) -> None:
    """Check the family's harness, prompts, and task-system vendor metadata.

    Raises
    ------
    ValueError
        The harness is one `check_harness` refuses, a system has a prompt that
        it cannot send, or a task system has no vendor metadata.
    """
    check_harness(family)
    if family.source == 'tasks':
        unowned = [system.name for system in systems if system.vendor is None]
        if unowned:
            raise ValueError(
                f'Family `{family.name}`: set vendor metadata for {code(unowned)} '
                'in the model catalog or their `route` tables in `systems.toml`.'
            )

    harness = family.settings.harness
    if harness is None or HARNESSES[harness].sends_prompt:
        return

    prompted = [system.name for system in systems if system.prompt is not None]
    if prompted:
        raise ValueError(
            f'Family `{family.name}` lists {code(prompted)}, whose prompt the '
            f'`{harness}` harness cannot send. Remove them from `systems`.'
        )


def family_systems(family: Family, systems: list[System]) -> list[System]:
    """Return the systems as the family runs them.

    A task family runs every system under its `harness`, which enters the
    system's fingerprint, while a case family runs each system as it resolves.
    """
    harness = family.settings.harness
    if harness is None:
        return systems
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
    current = {system.name: fingerprint(system) for system in systems}
    recorded: defaultdict[str, set[str]] = defaultdict(set)
    for trial in load_source_trials(_sources(family)):
        kwargs = trial.result.config.agent.kwargs
        name = kwargs.get('system')
        label = kwargs.get('fingerprint')
        if name in current and isinstance(label, str):
            recorded[name].add(label)

    return sorted(
        (name, label, current[name])
        for name, labels in recorded.items()
        for label in labels
        if label != current[name]
    )


def _agent_config(system: System) -> trial_config.AgentConfig:
    harness = HARNESSES[system.harness]
    return trial_config.AgentConfig(
        import_path=harness.import_path,
        model_name=harness.model_name(system.route),
        n_concurrent=ROUTE_CONCURRENCY,
        concurrency_group=system.route,
        kwargs=agent_kwargs(system),
    )


def job(family: Family, systems: list[System], tasks: list[Path]) -> JobConfig:
    """Build a job that runs every system on every task of the family.

    The systems are as `family_systems` returns them. The job diffs against the
    family's earlier jobs, so Harbor runs only the trials that none of them
    completed with the same task version and agent config. A task's tests grade
    its trials whatever the scorer, while a rendered case has no tests, so the
    verifier runs only for a task family, which also runs fewer trials at once. A
    job whose harnesses are all single-call runs in the host environment, which
    starts no container, and any other job runs in Harbor's Docker environment.
    """
    agents = [_agent_config(system) for system in systems]
    sources = [source for source, _ in _sources(family)]
    tasks_source = family.source == 'tasks'
    return _job(
        family,
        data_dir() / 'jobs' / family.name,
        agents,
        tasks,
        sources,
        environment=_environment(systems),
        n_attempts=family.settings.samples,
        n_concurrent=N_CONCURRENT_CONTAINERS if tasks_source else N_CONCURRENT_TRIALS,
        verify=tasks_source,
    )


def _environment(systems: list[System]) -> trial_config.EnvironmentConfig:
    if all(HARNESSES[system.harness].single_call for system in systems):
        return trial_config.EnvironmentConfig(import_path=HOST_ENVIRONMENT)
    return trial_config.EnvironmentConfig(type=EnvironmentType.DOCKER)


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

    changed: set[tuple[str, str]] = set()
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
    problems: list[str] = []
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


class TaskAuthor(BaseModel):
    label: str = Field(alias='author', min_length=1)
    vendor: str = Field(alias='author_vendor', min_length=1)


def author_vendors(
    family: Family,
    tasks: list[Path],
    systems: dict[str, System],
    export: NixosConfigExport,
) -> dict[str, str]:
    """Validate author provenance independently of runnable systems."""
    known = {
        model.vendor
        for model in export.catalog.models.values()
        if model.vendor is not None
    }
    known.update(
        system.vendor for system in systems.values() if system.vendor is not None
    )
    authors: dict[str, str] = {}
    problems: list[str] = []
    for task in tasks:
        location = f'`{task_location(family, task)}/task.toml`'
        try:
            author = TaskAuthor.model_validate(_task_config(task).metadata)
        except ValidationError as error:
            problems.append(error_lines(error, location))
            continue
        if author.vendor not in known:
            problems.append(
                f'{location}: author vendor `{author.vendor}` is not one of '
                f'{code(sorted(known))}.'
            )
        else:
            authors[task.name] = author.vendor

    if problems:
        raise ValueError(
            f'Family `{family.name}` has authors to fix:\n' + '\n'.join(problems)
        )
    return authors


def _task_problems(config: task_config.TaskConfig, location: str) -> list[str]:
    problems: list[str] = []
    if config.task is None or config.task.version is None:
        problems.append(f'{location}: set `[task] version`.')
    elif not semver.Version.is_valid(config.task.version):
        problems.append(
            f'{location}: `[task] version` `{config.task.version}` is not a '
            'semantic version, such as `1.0.0`.'
        )

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

    workspaces = [
        artifact
        for artifact in config.artifacts
        if (artifact if isinstance(artifact, str) else artifact.source) == WORKSPACE
    ]

    if not workspaces:
        problems.append(f'{location}: list `"{WORKSPACE}"` under `[[artifacts]]`.')
    if any(
        not isinstance(artifact, str) and artifact.destination is not None
        for artifact in workspaces
    ):
        problems.append(
            f'{location}: remove the `destination` of the `"{WORKSPACE}"` '
            'artifact, since collection reads the workspace at its source path.'
        )

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
        environment=trial_config.EnvironmentConfig(type=EnvironmentType.DOCKER),
        n_attempts=CHECK_ATTEMPTS,
        n_concurrent=N_CONCURRENT_CONTAINERS,
        verify=True,
    )


def has_jobs(family: Family) -> bool:
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
    environment: trial_config.EnvironmentConfig,
    n_attempts: int,
    n_concurrent: int,
    verify: bool,
) -> JobConfig:
    retry = RetryConfig(max_retries=1)
    retry.exclude_exceptions = (retry.exclude_exceptions or set()) | {
        ResponseCompletionError.__name__,
        MessageCompletionError.__name__,
    }

    return JobConfig(
        jobs_dir=jobs_dir,
        n_attempts=n_attempts,
        n_concurrent_trials=n_concurrent,
        quiet=True,
        retry=retry,
        environment=environment,
        verifier=trial_config.VerifierConfig(disable=not verify),
        agents=agents,
        tasks=[trial_config.TaskConfig(path=path) for path in tasks],
        source_jobs=sources,
    )


def job_path(config: JobConfig) -> Path:
    return config.jobs_dir / config.job_name


def run_job(config: JobConfig) -> Path:
    """Run the job with the Harbor CLI and return the job directory.

    Harbor inherits the process environment, which must hold `HARBOR_ENV` and
    lack `ANTHROPIC_CUSTOM_HEADERS`, as `bench` arranges.

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


@dataclass(frozen=True)
class _Candidate:
    """A trial's verdict and originating solution, before reading its artifacts."""

    trial: SourceTrial
    origin: SourceTrial
    case: str

    @property
    def system(self) -> str:
        result = self.origin.result
        system: str = result.config.agent.kwargs.get('system', result.agent_info.name)
        return system

    @property
    def fingerprint(self) -> str | None:
        label: str | None = self.origin.result.config.agent.kwargs.get('fingerprint')
        return label

    @property
    def job(self) -> str:
        return self.origin.trial_dir.parent.name

    @property
    def name(self) -> str:
        return self.origin.result.trial_name

    @property
    def error(self) -> str | None:
        exception = self.trial.result.exception_info
        return exception.exception_type if exception else None


def collect(family: Family, systems: dict[str, System]) -> list[Output]:
    """Read the family's trials into `outputs/<family>.jsonl`.

    A case family requires the current source and render contract. A task family
    keeps only trials of its current tasks at a version Harbor would reuse, so a
    patch bump keeps the trials Harbor reused, while a minor bump keeps only their
    regrades. Each case, system, and fingerprint keeps at most `samples` outputs,
    keeping only trials whose recorded generation, environment, and verifier
    settings match the current job. A successful trial replaces an errored one,
    while equivalent successful trials keep their original order.
    Costs come from the current catalog price. A system without a price reports
    as unpriced.

    A regraded trial yields the output of the trial whose agent wrote its
    solution, with the regrade's verdict, so the votes on that output stay. Harbor
    can regrade one trial twice, so each solution keeps only its first output.

    Raises
    ------
    ValueError
        A source job is not a job directory, a trial record is unreadable, a
        task is invalid, or the harness is one `check_harness` refuses.
    """
    check_harness(family)
    cases = {case.id: case for case in load_cases(family)}
    expected = _inputs(family, list(systems.values()))
    candidates = [
        candidate
        for candidate in _read_trials(
            family, _sources(family), _current_versions(family, cases)
        )
        if (
            candidate.trial.lock.agent,
            candidate.trial.lock.environment,
            candidate.trial.lock.verifier.disable,
        )
        == expected.get(candidate.system)
        and candidate.origin.lock.agent == candidate.trial.lock.agent
        and candidate.origin.lock.environment == candidate.trial.lock.environment
    ]

    candidates.sort(
        key=lambda candidate: (
            candidate.error is not None,
            candidate.job,
            candidate.name,
        )
    )

    groups: defaultdict[tuple[str, str, str | None], list[_Candidate]] = defaultdict(
        list
    )

    solutions: set[tuple[str, str]] = set()
    for candidate in candidates:
        if (candidate.job, candidate.name) in solutions:
            continue
        solutions.add((candidate.job, candidate.name))
        groups[(candidate.case, candidate.system, candidate.fingerprint)].append(
            candidate
        )

    outputs = sorted(
        (
            _output(family, candidate, systems, cases[candidate.case].instruction)
            for group in groups.values()
            for candidate in group[: family.settings.samples]
        ),
        key=lambda output: (output.case, output.system, output.job, output.trial),
    )

    write_outputs(family.name, outputs)
    return outputs


def _inputs(
    family: Family, systems: list[System]
) -> dict[str, tuple[trial_config.AgentConfig, trial_config.EnvironmentConfig, bool]]:
    """Return the current agent, environment, and verifier-disable inputs.

    Only systems currently listed by the family can contribute outputs.
    """
    resolved = family_systems(
        family, [system for system in systems if system.name in family.settings.systems]
    )
    environment = _environment(resolved)
    return {
        system.name: (
            _agent_config(system),
            environment,
            family.source != 'tasks',
        )
        for system in resolved
    }


def read_trials(
    family: Family, job_dir: Path, systems: dict[str, System]
) -> list[Output]:
    """Return one output per finished trial whose case is still current.

    Raises
    ------
    ValueError
        The directory is not a job directory, or a trial record is unreadable.
    """
    cases = {case.id: case for case in load_cases(family)}
    return [
        _output(family, candidate, systems, cases[candidate.case].instruction)
        for candidate in _read_trials(family, [_source(job_dir)])
        if candidate.case in cases
    ]


def _read_trials(
    family: Family,
    sources: list[tuple[SourceJobConfig, Path]],
    versions: dict[str, str] | None = None,
) -> list[_Candidate]:
    """Return each kept trial's verdict, origin, and selection metadata.

    A `versions` mapping drops a trial whose task version is not current before
    an output reads that task.
    """
    trials = load_source_trials(sources)
    by_id = {trial.id: trial for trial in trials}
    records: list[_Candidate] = []
    for trial in trials:
        case = trial.task_name.removeprefix(f'{family.name}/')
        if versions is not None and not _is_current(
            family, trial.lock.task.version, versions.get(case)
        ):
            continue
        records.append(_Candidate(trial, _origin(trial, by_id), case))

    return records


def _current_versions(family: Family, cases: dict[str, Case]) -> dict[str, str]:
    """Return expected current task versions without changing rendered tasks."""
    match family.source:
        case 'cases':
            versions: dict[str, str] = {}
            for case in cases.values():
                package = render_task_config(
                    f'{family.name}/{case.id}', case.instruction
                ).task
                assert package is not None and package.version is not None
                versions[case.id] = package.version
            return versions
        case 'tasks':
            tasks = [case_task(family, case) for case in cases]
        case _:
            assert_never(family.source)

    return {
        task.name: version
        for task in tasks
        if (task / 'task.toml').is_file() and (version := _task_version(task))
    }


def _is_current(family: Family, version: str | None, current: str | None) -> bool:
    """Return whether a trial's task version matches its task's current version.

    A rendered case matches exactly, since its build metadata hashes its content.
    A task also matches a version with the same major and minor but another
    patch, which Harbor reuses whatever the pre-release and build metadata say,
    while it reruns or regrades any other difference.
    """
    if version is None or current is None:
        return False
    if version == current or family.source == 'cases':
        return version == current

    recorded = semver.Version.parse(version)
    installed = semver.Version.parse(current)
    return (recorded.major, recorded.minor) == (
        installed.major,
        installed.minor,
    ) and recorded.patch != installed.patch


def _origin(trial: SourceTrial, trials: dict[UUID, SourceTrial]) -> SourceTrial:
    """Return the trial whose agent wrote the trial's solution.

    A regrade names its source trial, which may be a regrade itself. A source
    outside `trials` ends the chain at the regrade, which holds copies of its
    source's agent result and artifacts, with symlinks dereferenced.
    """
    while (source := trial.result.config.source_trial) is not None:
        trial_id = source.trial_id
        if trial_id is None or trial_id not in trials:
            break

        trial = trials[trial_id]

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
    candidate: _Candidate,
    systems: dict[str, System],
    instruction: str,
) -> Output:
    """Materialize the originating solution with its selected trial's verdict."""
    trial, origin, case = candidate.trial, candidate.origin, candidate.case
    result = origin.result
    context = result.agent_result or AgentContext()
    metadata = context.metadata or {}
    exception = trial.result.exception_info
    reward = _reward(trial.result) if family.source == 'tasks' else None
    text = _text(family, origin, case, reward)
    finish_reason = metadata.get('finish_reason')

    return Output(
        trial=candidate.name,
        job=candidate.job,
        case=case,
        system=candidate.system,
        fingerprint=candidate.fingerprint,
        nixos_config_rev=metadata.get('nixos_config_rev'),
        harness_version=metadata.get('harbor_version'),
        error=candidate.error,
        text=text,
        reward=reward,
        gate_failures=_gate_failures(
            family,
            instruction,
            text,
            finish_reason,
            reward,
            errored=exception is not None,
        ),
        finish_reason=finish_reason,
        stop_details=metadata.get('stop_details'),
        input_tokens=context.n_input_tokens,
        cached_tokens=context.n_cache_tokens,
        cache_write_tokens=metadata.get('cache_write_tokens'),
        output_tokens=context.n_output_tokens,
        reasoning_tokens=metadata.get('reasoning_tokens'),
        cost_usd=_cost(systems.get(candidate.system), context),
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
            match family.settings:
                case VotesSettings() if reward is not None:
                    artifacts = TrialPaths(origin.trial_dir).artifacts_dir
                    return workspace_diff(
                        case_task(family, case) / 'environment' / 'app',
                        source_host_path(artifacts, WORKSPACE),
                    )
                case VotesSettings() | TaskTestsSettings():
                    return None
                case _:
                    assert_never(family.settings)
        case _:
            assert_never(family.source)


def _gate_failures(
    family: Family,
    instruction: str,
    text: str | None,
    finish_reason: str | None,
    reward: float | None,
    *,
    errored: bool,
) -> list[Gate] | None:
    """Return the gates a voted output fails, or `None` when it has no verdict.

    A task's tests give the verdict even when its agent errored.
    """
    match family.settings:
        case VotesSettings():
            if family.source == 'tasks':
                if reward is None:
                    return None
                return [] if tests_pass(reward) else [Gate.TESTS]
            if errored:
                return None

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
        or (context.metadata or {}).get('cache_write_unpriced', False)
    ):
        return None
    return system.price.cost(
        context.n_input_tokens,
        context.n_cache_tokens or 0,
        context.n_output_tokens,
        (context.metadata or {}).get('cache_write_tokens', 0),
    )
