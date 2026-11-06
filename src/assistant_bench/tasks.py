"""Harbor tasks: rendering cases, checking authored tasks, and their versions."""

import hashlib
import shutil
from pathlib import Path
from typing import assert_never

import semver
from harbor.models.task import config as task_config
from harbor.models.task.verifier_mode import resolve_task_verifier_mode
from harbor.models.trial import config as trial_config
from harbor.publisher.packager import Packager
from harbor.trial.network_policy import resolve_trial_network_plan
from pydantic import BaseModel, Field, ValidationError

from assistant_bench.catalog import NixosConfigExport
from assistant_bench.families import Case, Family, case_task, task_location, tasks_dir
from assistant_bench.jobs import family_sources, source_trials
from assistant_bench.messages import InputError, code, error_lines


# Never bump the patch: Harbor and collection reuse a trial at another patch
# whatever its build metadata, so every edited case would keep its trials.
TASK_VERSION = '1.0.0'
AGENT_TIMEOUT_SEC = 3600.0
WORKSPACE = '/app'


# ── Rendering ──


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
    verifier is disabled. A task's version follows its content. The directory is
    rebuilt, so cases the family no longer has leave no task.
    """
    directory = tasks_dir(family)
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True)
    return [_render_task(family, case, directory / case.id) for case in cases]


def _render_task(family: Family, case: Case, task_dir: Path) -> Path:
    task_dir.mkdir(parents=True)
    (task_dir / 'instruction.md').write_text(case.instruction)
    config = render_task_config(f'{family.name}/{case.id}', case.instruction)
    (task_dir / 'task.toml').write_text(config.model_dump_toml())
    return task_dir


# ── Checks ──


def check_tasks(family: Family, tasks: list[Path]) -> None:
    """Check that every task sets what a run needs.

    Raises
    ------
    InputError
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
        raise InputError(
            f'Family `{family.name}` has tasks to fix:\n' + '\n'.join(problems)
        )


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
            task_cfg=config,
            trial_agent_cfg=trial_config.AgentConfig(),
            trial_env_cfg=trial_config.EnvironmentConfig(),
            trial_verifier_cfg=trial_config.VerifierConfig(),
            step_cfg=None,
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


class TaskAuthor(BaseModel):
    label: str = Field(alias='author', min_length=1)
    vendor: str = Field(alias='author_vendor', min_length=1)


def author_vendors(
    family: Family, tasks: list[Path], export: NixosConfigExport
) -> dict[str, str]:
    """Validate author provenance independently of runnable systems."""
    known = {model.vendor for model in export.catalog.models.values()}
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
        raise InputError(
            f'Family `{family.name}` has authors to fix:\n' + '\n'.join(problems)
        )
    return authors


# ── Versions ──


def changed_tasks(family: Family, tasks: list[Path]) -> list[tuple[str, str]]:
    """Return each task whose content changed since a trial recorded its version.

    Harbor aborts a job whose task has an earlier trial at the same version but
    another digest. An entry names the task and that version, sorted by task.

    Raises
    ------
    InputError
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
    for trial in source_trials(family_sources(family)):
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


def current_versions(family: Family, cases: dict[str, Case]) -> dict[str, str | None]:
    """Return expected current task versions without changing rendered tasks."""
    match family.source:
        case 'cases':
            return {case.id: _rendered_version(family, case) for case in cases.values()}
        case 'tasks':
            return {case: _task_version(case_task(family, case)) for case in cases}
        case _:
            assert_never(family.source)


def _rendered_version(family: Family, case: Case) -> str | None:
    package = render_task_config(f'{family.name}/{case.id}', case.instruction).task
    return package.version if package is not None else None


def _task_version(task_dir: Path) -> str | None:
    package = _task_config(task_dir).task
    return package.version if package is not None else None


def _task_config(task_dir: Path) -> task_config.TaskConfig:
    return task_config.TaskConfig.model_validate_toml(
        (task_dir / 'task.toml').read_text()
    )
