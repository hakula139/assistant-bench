"""Harbor job configs for a family's systems, and their runs."""

import asyncio
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID

from harbor.job_diff import (
    DiffAction,
    SourceTrial,
    compare_job_trials,
    load_source_trials,
    resolve_diff_sources,
)
from harbor.job_plan import JobPlan
from harbor.models.environment_type import EnvironmentType
from harbor.models.job.config import JobConfig, RetryConfig, SourceJobConfig
from harbor.models.job.lock import ExtraDockerComposeLock
from harbor.models.trial import config as trial_config
from harbor.models.trial.result import TrialResult

from assistant_bench.artifacts import (
    ArtifactRef,
    ArtifactRepository,
)
from assistant_bench.catalog import SYSTEMS_FILE, System
from assistant_bench.families import (
    CheckersSettings,
    Family,
    case_task,
    family_names,
    load_cases,
    load_family,
)
from assistant_bench.harnesses import (
    HARNESSES,
    HOST_ENVIRONMENT,
    agent_kwargs,
    fingerprint,
)
from assistant_bench.harnesses.cache import InstalledHarness, artifact
from assistant_bench.harnesses.docker import HOST_OVERLAY, check_local_context
from assistant_bench.harnesses.endpoint import ENDPOINT_VARS
from assistant_bench.harnesses.native import NativeCompletionError
from assistant_bench.messages import InputError, code
from assistant_bench.state import ArtifactGeneration, State


N_CONCURRENT_TRIALS = 8
# A task trial can hold both an agent and a verifier container.
N_CONCURRENT_CONTAINERS = 4
# Harbor's reuse key covers the whole agent config, so changing this reruns every
# trial, as does renaming a system.
ROUTE_CONCURRENCY = 4
# LiteLLM loads a .env above its own install unless its mode is PRODUCTION, and
# bench loads the working directory's .env itself. Importing LiteLLM downloads its
# cost map unless told to read the bundled copy.
HARBOR_ENV = {
    'HARBOR_TELEMETRY': 'off',
    'LITELLM_LOCAL_MODEL_COST_MAP': 'true',
    'LITELLM_MODE': 'PRODUCTION',
}


# ── Systems ──


def check_harness(family: Family) -> None:
    """Check that a task family's harness can solve its tasks.

    Raises
    ------
    InputError
        The family names an unknown harness or a single-call one.
    """
    harness = family.settings.harness
    if harness is None:
        return
    if harness not in HARNESSES:
        raise InputError(
            f'Family `{family.name}`: harness `{harness}` is not one of '
            f'{code(HARNESSES)}.'
        )
    if HARNESSES[harness].single_call:
        agents = [name for name, entry in HARNESSES.items() if not entry.single_call]
        raise InputError(
            f'Family `{family.name}`: harness `{harness}` makes one request and '
            f"never acts in a task's environment, so choose among {code(agents)}."
        )


def listed_systems(family: Family, systems: Mapping[str, System]) -> list[System]:
    """Return the systems a family lists, refusing a name `systems.toml` lacks."""
    unknown = [name for name in family.settings.systems if name not in systems]
    if unknown:
        raise InputError(
            f'Family `{family.name}` lists {code(unknown)}, which '
            f'`{SYSTEMS_FILE}` does not define. Fix '
            f'`families/{family.name}/family.toml` or add the system table.'
        )
    return [systems[name] for name in family.settings.systems]


def check_systems(family: Family, systems: list[System]) -> None:
    """Check the family's harness and its systems' prompts.

    Raises
    ------
    InputError
        The harness is one `check_harness` refuses, or a system has a prompt
        that it cannot send.
    """
    check_harness(family)
    if isinstance(family.settings, CheckersSettings):
        by_name = {system.name: system for system in systems}
        excluded = {'name', 'display_name', 'prompt', 'price', 'vendor'}
        for pair in family.settings.pairs:
            bare, instructed = by_name[pair.bare], by_name[pair.prompted]
            if (
                bare.prompt is not None
                or instructed.prompt is None
                or bare.model_dump(exclude=excluded)
                != instructed.model_dump(exclude=excluded)
            ):
                raise InputError(
                    f'Family `{family.name}`: `{pair.bare}` and `{pair.prompted}` '
                    'must differ only by the added prompt and public labels.'
                )
    for system in family_systems(family, systems):
        harness = HARNESSES[system.harness]
        if family.source != 'tasks' and not harness.single_call:
            raise InputError(
                f'Family `{family.name}`: system `{system.name}` uses '
                f'`{system.harness}`, which requires a task environment.'
            )
        if system.api not in harness.apis:
            raise InputError(
                f'System `{system.name}`: harness `{system.harness}` accepts '
                f'{code(harness.apis)} (got `{system.api}`).'
            )
        if system.harness in ('claude-code', 'codex') and system.version is None:
            raise InputError(
                f'System `{system.name}`: set its `harness` and pinned `version` '
                'in `systems.toml`.'
            )
        if system.prompt is not None and not harness.sends_prompt:
            raise InputError(
                f'Family `{family.name}` lists {code([system.name])}, whose prompt the '
                f'`{system.harness}` harness cannot send. Remove them from `systems`.'
            )


def family_systems(family: Family, systems: list[System]) -> list[System]:
    """Return the systems as the family runs them.

    A task family's default harness applies to systems without an explicit
    installed harness. A case family keeps each system as it resolves.
    """
    harness = family.settings.harness
    if harness is None:
        return systems
    return [
        system if system.version else system.model_copy(update={'harness': harness})
        for system in systems
    ]


def runnable_systems(systems: Mapping[str, System]) -> dict[str, list[System]]:
    """Map each family to its runnable systems as the family runs them."""
    runnable: dict[str, list[System]] = {}
    for name in family_names():
        family = load_family(name)
        runnable[name] = family_systems(family, listed_systems(family, systems))
    return runnable


def drifted_systems(
    family: Family,
    systems: list[System],
    sources: list[tuple[SourceJobConfig, Path]],
) -> list[tuple[str, str, str]]:
    """Return each selected system whose earlier trials recorded another fingerprint.

    The systems are as `family_systems` returns them. An entry names the system,
    the fingerprint an earlier trial recorded, and the fingerprint the system
    resolves to now. A system can contribute one entry per recorded fingerprint,
    and the list is sorted by system name.

    Raises
    ------
    InputError
        A source job is not a job directory, or a trial record is unreadable.
    """
    current = {system.name: fingerprint(system) for system in systems}
    recorded: defaultdict[str, set[str]] = defaultdict(set)
    for trial in source_trials(sources):
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


# ── Jobs ──


def _agent_config(system: System) -> trial_config.AgentConfig:
    harness = HARNESSES[system.harness]
    return trial_config.AgentConfig(
        import_path=harness.import_path,
        model_name=f'{harness.model_prefix}{system.route}',
        extra_allowed_hosts=['host.docker.internal'] if system.version else [],
        n_concurrent=ROUTE_CONCURRENCY,
        concurrency_group=system.route,
        kwargs=agent_kwargs(system),
    )


def job(
    family: Family,
    systems: list[System],
    tasks: list[Path],
    *,
    root: Path,
    sources: list[tuple[SourceJobConfig, Path]],
) -> JobConfig:
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
    source_jobs = [config for config, _ in sources]
    tasks_source = family.source == 'tasks'
    return _job(
        jobs_dir(family, root=root),
        agents,
        tasks,
        source_jobs,
        environment=_environment(systems, family.source_root.parent),
        n_attempts=family.settings.samples,
        n_concurrent=N_CONCURRENT_CONTAINERS if tasks_source else N_CONCURRENT_TRIALS,
        verify=tasks_source,
    )


def _environment(
    systems: list[System], source_root: Path | None = None
) -> trial_config.EnvironmentConfig:
    if all(HARNESSES[system.harness].single_call for system in systems):
        return trial_config.EnvironmentConfig(
            import_path=HOST_ENVIRONMENT, kwargs={'auto_mounting': True}
        )
    if any(system.version for system in systems):
        return trial_config.EnvironmentConfig(
            import_path='assistant_bench.harnesses.docker:InstalledDocker',
            extra_docker_compose=[
                source_root
                / 'src'
                / 'assistant_bench'
                / 'harnesses'
                / HOST_OVERLAY.name
                if source_root is not None
                else HOST_OVERLAY
            ],
        )
    return trial_config.EnvironmentConfig(type=EnvironmentType.DOCKER)


def probe_job(system: System, task: Path, root: Path) -> JobConfig:
    """Build one fresh single-call probe with no Harbor retries or diff sources."""
    config = _job(
        root,
        [_agent_config(system).model_copy(update={'n_concurrent': 1})],
        [task],
        [],
        environment=_environment([system]),
        n_attempts=1,
        n_concurrent=1,
        verify=False,
    )
    return config.model_copy(update={'retry': RetryConfig(max_retries=0)})


type EnvironmentInputs = tuple[
    trial_config.EnvironmentConfig, tuple[ExtraDockerComposeLock, ...] | None
]


def environment_inputs(
    environment: trial_config.EnvironmentConfig,
    overlays: list[ExtraDockerComposeLock] | None,
) -> EnvironmentInputs:
    """Use Harbor's ordered content identity for portable Compose locations."""
    return (
        environment.model_copy(update={'extra_docker_compose': []}),
        tuple(overlays) if overlays is not None else None,
    )


def current_inputs(
    family: Family, systems: list[System]
) -> dict[str, tuple[trial_config.AgentConfig, EnvironmentInputs, bool]]:
    """Map each system the family lists to the inputs a current trial records.

    The inputs are the agent config, the environment config, and whether the
    verifier is disabled.
    """
    resolved = family_systems(
        family, [system for system in systems if system.name in family.settings.systems]
    )
    environment = _environment(resolved, family.source_root.parent)
    overlays: list[ExtraDockerComposeLock] = []
    for path in environment.extra_docker_compose:
        with path.open('rb') as file:
            digest = hashlib.file_digest(file, 'sha256').hexdigest()
        overlays.append(ExtraDockerComposeLock(path=path, digest=f'sha256:{digest}'))
    identity = environment_inputs(environment, overlays or None)
    return {
        system.name: (
            _agent_config(system),
            identity,
            family.source != 'tasks',
        )
        for system in resolved
    }


def check_job(family: Family, tasks: list[Path], *, root: Path) -> JobConfig:
    """Build a job that runs the oracle and the nop on every task of the family.

    The oracle must pass every task while the untouched repository must fail it.
    Its trials go under `checks/`, apart from the jobs that `collect` reads.
    """
    return _job(
        root / 'checks' / family.name,
        [trial_config.AgentConfig(name=agent) for agent in ('oracle', 'nop')],
        tasks,
        [],
        environment=trial_config.EnvironmentConfig(type=EnvironmentType.DOCKER),
        n_attempts=1,
        n_concurrent=N_CONCURRENT_CONTAINERS,
        verify=True,
    )


def jobs_dir(family: Family, *, root: Path) -> Path:
    return root / 'jobs' / family.name


def job_source(job_dir: Path) -> tuple[SourceJobConfig, Path]:
    return SourceJobConfig(action='diff', type='local', path=job_dir), job_dir


@dataclass(frozen=True)
class _SourceView:
    job: JobConfig
    result: TrialResult
    config: trial_config.TrialConfig
    directory: Path


def materialize_sources(
    family: Family,
    state: State,
    repository: ArtifactRepository,
    root: Path,
    *,
    private_root: Path,
    generation: ArtifactGeneration,
) -> list[tuple[SourceJobConfig, Path]]:
    """Restore registered trials and prepare separate portable Harbor views."""
    tasks = {
        f'{family.name}/{case.id}': (
            root / 'tasks' / family.name / case.id
            if family.source == 'cases'
            else case_task(family, case.id, private_root=private_root)
        )
        for case in load_cases(family, private_root=private_root)
    }
    directory = root / 'sources' / family.name
    directory.mkdir(parents=True, exist_ok=True)
    runs = {
        run_id: state.run(run_id)
        for run_id in dict.fromkeys(row.run_id for row in generation.artifacts)
    }
    restorations = [
        (
            ArtifactRef(
                commit_sha=row.commit_sha,
                path=row.path,
                manifest_digest=row.manifest_digest,
            ),
            Path(tempfile.mkdtemp(prefix='raw-', dir=directory)),
        )
        for row in generation.artifacts
    ]
    archives = repository.materialize_trials(restorations)
    views: dict[UUID, _SourceView] = {}
    references: dict[UUID, ArtifactRef] = {}
    jobs: dict[Path, list[Path]] = {}
    for row, (reference, _), archive in zip(
        generation.artifacts, restorations, archives, strict=True
    ):
        run = runs[row.run_id]
        manifest = archive.manifest
        job = JobConfig.model_validate_json((archive.job / 'config.json').read_text())
        result = TrialResult.model_validate_json(
            (archive.job / manifest.trial_name / 'result.json').read_text()
        )
        if (
            manifest.scope != family.name
            or manifest.run_id != run.id
            or manifest.job_name != run.job
            or job.job_name != run.job
            or manifest.code_revision != run.code_revision
            or manifest.case_revision != run.case_revision
            or manifest.trial_id != str(result.id)
            or str(result.id) != row.source_trial_id
            or manifest.trial_name != row.trial_id
            or result.trial_name != manifest.trial_name
        ):
            raise InputError('Registered trial artifacts have conflicting identities.')
        if result.id in references:
            if references[result.id] != reference:
                raise InputError('A source trial has conflicting artifact references.')
            continue
        references[result.id] = reference
        if result.task_name not in tasks:
            continue
        config = trial_config.TrialConfig.model_validate_json(
            (archive.job / manifest.trial_name / 'config.json').read_text()
        )
        view_dir = Path(tempfile.mkdtemp(prefix='view-', dir=directory)) / run.job
        shutil.copytree(archive.job, view_dir, symlinks=True)
        views[result.id] = _SourceView(job, result, config, view_dir)
        jobs.setdefault(result.config.trials_dir, []).append(view_dir)

    paths = {
        trial_id: view.directory / view.result.trial_name
        for trial_id, view in views.items()
    }
    for view in views.values():
        _relocate_source(view, tasks[view.result.task_name], paths, jobs)
    return [job_source(view.directory) for view in views.values()]


def _relocate_source(
    view: _SourceView,
    task: Path,
    trials: dict[UUID, Path],
    jobs: dict[Path, list[Path]],
) -> None:
    def relocate(config: trial_config.TrialConfig) -> trial_config.TrialConfig:
        source = config.source_trial
        if source is not None:
            if source.trial_id is None or source.trial_id not in trials:
                raise InputError('A regraded trial has no registered source artifact.')
            source = source.model_copy(
                update={'type': 'local', 'path': trials[source.trial_id]}
            )
        return config.model_copy(
            update={
                'task': config.task.model_copy(update={'path': task}),
                'trials_dir': view.directory,
                'source_trial': source,
            }
        )

    config = relocate(view.config)
    result = view.result.model_copy(
        update={
            'config': relocate(view.result.config),
            'task_id': config.task.get_task_id(),
            'trial_uri': (view.directory / view.result.trial_name).as_uri(),
        }
    )
    job = view.job.model_copy(
        update={
            'jobs_dir': view.directory.parent,
            'tasks': [result.config.task],
            'agents': [result.config.agent],
            'source_jobs': [
                source.model_copy(update={'type': 'local', 'path': path})
                for source in view.job.source_jobs
                if source.path is not None
                for path in jobs.get(source.path, [])
            ],
        }
    )
    (view.directory / 'config.json').write_text(job.model_dump_json(indent=2))
    trial = view.directory / result.trial_name
    (trial / 'config.json').write_text(config.model_dump_json(indent=2))
    (trial / 'result.json').write_text(result.model_dump_json(indent=2))


def source_trials(sources: list[tuple[SourceJobConfig, Path]]) -> list[SourceTrial]:
    """Load the trials of the source jobs.

    Raises
    ------
    InputError
        A source job is not a job directory, or a trial record is unreadable.
    """
    try:
        return load_source_trials(sources)
    except ValueError as error:
        raise InputError(str(error)) from error


def _job(
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
    retry = RetryConfig(max_retries=0)
    retry.exclude_exceptions = (retry.exclude_exceptions or set()) | {
        NativeCompletionError.__name__
    }

    return JobConfig(
        # Harbor's default collides within a second.
        job_name=f'{datetime.now():%Y-%m-%d__%H-%M-%S-%f}',
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


def pending_trials(config: JobConfig) -> Counter[str]:
    """Count each system's trials that the job would run its agent for.

    Harbor's own diff classifies the trials, so a reused or regraded trial, which
    sends no model request, counts nothing.
    """
    if not config.tasks:
        return Counter()
    plan = asyncio.run(JobPlan.from_config(config.model_copy(deep=True)))
    _, sources = (
        asyncio.run(resolve_diff_sources(config.source_jobs, jobs_dir=config.jobs_dir))
        if config.is_diff
        else ([], [])
    )
    diff = compare_job_trials(plan, sources)
    return Counter(
        trial.agent.kwargs['system']
        for trial, action in zip(plan.trial_configs, diff.trials, strict=True)
        if action.action == DiffAction.RERUN
    )


def job_path(config: JobConfig) -> Path:
    return config.jobs_dir / config.job_name


def run_job(
    config: JobConfig,
    *,
    log: Path | None = None,
    plugin_context: Path | None = None,
    source_root: Path | None = None,
) -> Path:
    """Run the job with the Harbor CLI and return the job directory.

    Harbor receives endpoint settings and the host tools' environment without
    the assistant's authentication, model settings, or native configuration.
    A supplied `log` captures both streams in a private mode-0600 file.

    Raises
    ------
    subprocess.CalledProcessError
        Harbor exited with an error.
    """
    harbor = Path(sys.executable).with_name('harbor')
    installed: set[tuple[InstalledHarness, str]] = set()
    for agent in config.agents:
        if agent.import_path == 'assistant_bench.harnesses.installed:ClaudeCode':
            installed.add(('claude-code', agent.kwargs['version']))
        elif agent.import_path == 'assistant_bench.harnesses.installed:Codex':
            installed.add(('codex', agent.kwargs['version']))
    if installed:
        asyncio.run(check_local_context())
        architecture = subprocess.check_output(
            ['docker', 'info', '--format', '{{.Architecture}}'], text=True
        ).strip()
        for harness, version in installed:
            artifact(harness, version, architecture)
    with tempfile.NamedTemporaryFile(
        'w', suffix='.json', delete_on_close=False
    ) as file:
        file.write(config.model_dump_json())
        file.close()
        command: list[str | Path] = [harbor, 'run', '--config', file.name, '--yes']
        if plugin_context is not None:
            command += [
                '--plugin',
                'assistant_bench.execution:ExecutionPlugin',
                '--plugin-kwarg',
                f'context_file={plugin_context}',
            ]
        environment = harbor_environment()
        if source_root is not None:
            environment['PYTHONPATH'] = str(source_root / 'src')
        if log is None:
            subprocess.run(command, check=True, env=environment)
        else:
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open('w') as output:
                os.fchmod(output.fileno(), 0o600)
                subprocess.run(
                    command,
                    check=True,
                    env=environment,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                )

    return job_path(config)


def harbor_environment() -> dict[str, str]:
    """Return the host tools and file-backed endpoint settings Harbor needs."""
    names = {
        'PATH',
        'HOME',
        'USER',
        'TMPDIR',
        'LANG',
        'BENCH_NIXOS_CONFIG',
        'BENCH_DATA_DIR',
        'BENCH_DATA_REPOSITORY',
        'BENCH_D1_TOKEN_FILE',
        'BENCH_D1_ENV_FILE',
        *ENDPOINT_VARS,
    }
    return {
        name: value
        for name, value in os.environ.items()
        if name in names or name.startswith(('DOCKER_', 'XDG_'))
    } | HARBOR_ENV
