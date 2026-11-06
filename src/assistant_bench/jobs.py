"""Harbor job configs for a family's systems, and their runs."""

import subprocess
import sys
import tempfile
from collections import defaultdict
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from harbor.job_diff import SourceTrial, load_source_trials
from harbor.models.environment_type import EnvironmentType
from harbor.models.job.config import JobConfig, RetryConfig, SourceJobConfig
from harbor.models.trial import config as trial_config

from assistant_bench.catalog import SYSTEMS_FILE, System
from assistant_bench.families import Family, family_names, load_family
from assistant_bench.harnesses import (
    HARNESSES,
    HOST_ENVIRONMENT,
    agent_kwargs,
    fingerprint,
)
from assistant_bench.harnesses.native import NativeCompletionError
from assistant_bench.messages import InputError, code
from assistant_bench.paths import data_dir


N_CONCURRENT_TRIALS = 8
# A task trial can hold both an agent and a verifier container.
N_CONCURRENT_CONTAINERS = 4
# Harbor's reuse key covers the whole agent config, so changing this reruns every
# trial, as does renaming a system.
ROUTE_CONCURRENCY = 4
CHECK_ATTEMPTS = 3
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
    harness = family.settings.harness
    if harness is None or HARNESSES[harness].sends_prompt:
        return

    prompted = [system.name for system in systems if system.prompt is not None]
    if prompted:
        raise InputError(
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


def runnable_systems(systems: Mapping[str, System]) -> dict[str, list[System]]:
    """Map each family to its runnable systems as the family runs them."""
    runnable: dict[str, list[System]] = {}
    for name in family_names():
        family = load_family(name)
        runnable[name] = family_systems(family, listed_systems(family, systems))
    return runnable


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
    InputError
        A source job is not a job directory, or a trial record is unreadable.
    """
    current = {system.name: fingerprint(system) for system in systems}
    recorded: defaultdict[str, set[str]] = defaultdict(set)
    for trial in source_trials(family_sources(family)):
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
        model_name=system.route,
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
    sources = [config for config, _ in family_sources(family)]
    tasks_source = family.source == 'tasks'
    return _job(
        _jobs_dir(family),
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
        return trial_config.EnvironmentConfig(
            import_path=HOST_ENVIRONMENT, kwargs={'auto_mounting': True}
        )
    return trial_config.EnvironmentConfig(type=EnvironmentType.DOCKER)


def current_inputs(
    family: Family, systems: list[System]
) -> dict[str, tuple[trial_config.AgentConfig, trial_config.EnvironmentConfig, bool]]:
    """Map each system the family lists to the inputs a current trial records.

    The inputs are the agent config, the environment config, and whether the
    verifier is disabled.
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


def check_job(family: Family, tasks: list[Path]) -> JobConfig:
    """Build a job that runs the oracle and the nop on every task of the family.

    The oracle must pass every task while the untouched repository must fail it.
    Each agent runs `CHECK_ATTEMPTS` times on each task, which catches a task that
    passes or fails only sometimes. Its trials go under `checks/`, apart from the
    jobs that `collect` reads.
    """
    return _job(
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
    return sorted(result.parent for result in _jobs_dir(family).glob('*/result.json'))


def _jobs_dir(family: Family) -> Path:
    return data_dir() / 'jobs' / family.name


def family_sources(family: Family) -> list[tuple[SourceJobConfig, Path]]:
    return [job_source(job_dir) for job_dir in _family_jobs(family)]


def job_source(job_dir: Path) -> tuple[SourceJobConfig, Path]:
    return SourceJobConfig(action='diff', type='local', path=job_dir), job_dir


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
    retry = RetryConfig(max_retries=1)
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
