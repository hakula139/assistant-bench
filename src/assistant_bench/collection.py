"""Collection of a family's trials into output records."""

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, assert_never
from uuid import UUID

import semver
from harbor.job_diff import SourceTrial
from harbor.models.agent.context import AgentContext
from harbor.models.job.config import SourceJobConfig
from harbor.models.task.artifacts import source_host_path
from harbor.models.trial.paths import TrialPaths
from harbor.models.trial.result import TrialResult

from assistant_bench.catalog import System
from assistant_bench.diffs import workspace_diff
from assistant_bench.families import (
    Case,
    CheckersSettings,
    Family,
    Gate,
    TaskTestsSettings,
    VotesSettings,
    case_task,
    load_cases,
)
from assistant_bench.harnesses.generate import OUTPUT_FILE
from assistant_bench.harnesses.usage import MeasuredUsage
from assistant_bench.jobs import (
    check_harness,
    current_inputs,
    family_sources,
    job_source,
    source_trials,
)
from assistant_bench.outputs import Output, tests_pass, write_outputs
from assistant_bench.scoring.checkers import evaluate
from assistant_bench.scoring.gates import failed_gates
from assistant_bench.spend import Spend, summarize, update_ledger
from assistant_bench.tasks import WORKSPACE, current_versions


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

    @property
    def graded(self) -> bool:
        return _reward(self.trial.result) is not None


@dataclass(frozen=True)
class Collection:
    """The outputs a collection wrote, and the usage gaps of their systems."""

    outputs: list[Output]
    spend: list[Spend]


def collect(family: Family, systems: dict[str, System]) -> Collection:
    """Read the family's trials into `outputs/<family>.jsonl`, and its spend.

    A case family requires the current source and render contract. A task family
    keeps only trials of its current tasks at a version Harbor would reuse, so a
    patch bump keeps the trials Harbor reused, while a minor bump keeps only their
    regrades. Each case, system, and fingerprint keeps at most `samples` outputs,
    keeping only trials whose recorded generation, environment, and verifier
    settings match the current job. A successful trial comes first, then an
    errored trial its tests graded, then one they never graded, with the oldest
    first within each tier whatever its reward. Costs come from the current
    catalog price, and a system without a price reports as unpriced.

    A regraded trial yields the output of the trial whose agent wrote its
    solution, with the regrade's verdict, so the votes on that output stay. Harbor
    can regrade one trial twice, so each solution keeps only its first output.

    The spend summarizes the family's ledger once `update_ledger` has added the
    attempts of its jobs, which it does first, so a refusal leaves no attempt
    unrecorded.

    Raises
    ------
    InputError
        A source job is not a job directory, a trial record is unreadable, a
        task is invalid, or the harness is one `check_harness` refuses.
    """
    entries = update_ledger(family, systems)
    check_harness(family)
    cases = {case.id: case for case in load_cases(family)}
    expected = current_inputs(family, list(systems.values()))
    candidates = [
        candidate
        for candidate in _read_trials(
            family, family_sources(family), current_versions(family, cases)
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
            not candidate.graded,
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
            _output(
                family,
                candidate,
                systems.get(candidate.system),
                cases[candidate.case],
            )
            for group in groups.values()
            for candidate in group[: family.settings.samples]
        ),
        key=lambda output: (output.case, output.system, output.job, output.trial),
    )

    write_outputs(family.name, outputs)
    return Collection(outputs, summarize(entries))


def read_trials(family: Family, job_dir: Path) -> list[Output]:
    """Return one output per finished trial of a case the family still has.

    Raises
    ------
    InputError
        The directory is not a job directory, or a trial record is unreadable.
    """
    cases = {case.id: case for case in load_cases(family)}
    return [
        _output(family, candidate, None, cases[candidate.case])
        for candidate in _read_trials(family, [job_source(job_dir)])
        if candidate.case in cases
    ]


def _read_trials(
    family: Family,
    sources: list[tuple[SourceJobConfig, Path]],
    versions: dict[str, str | None] | None = None,
) -> list[_Candidate]:
    """Return each kept trial's verdict, origin, and selection metadata.

    A `versions` mapping drops a trial whose task version is not current before
    an output reads that task.
    """
    trials = source_trials(sources)
    by_id = {trial.id: trial for trial in trials}
    records: list[_Candidate] = []
    for trial in trials:
        case = trial.task_name.removeprefix(f'{family.name}/')
        if versions is not None and not _is_current(
            trial.lock.task.version, versions.get(case)
        ):
            continue
        records.append(_Candidate(trial, _origin(trial, by_id), case))

    return records


# Mirrors Harbor's job_diff._version_action
def _is_current(version: str | None, current: str | None) -> bool:
    """Return whether Harbor would reuse a trial at `version` for the current task.

    Harbor reuses a trial whose version equals the task's or has another patch,
    whatever its pre-release and build metadata say, and reruns or regrades any
    other difference.
    """
    if version is None or current is None:
        return False
    if version == current:
        return True

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


def _output(
    family: Family,
    candidate: _Candidate,
    system: System | None,
    source: Case,
) -> Output:
    """Materialize the originating solution with its selected trial's verdict."""
    trial, origin, case = candidate.trial, candidate.origin, candidate.case
    result = origin.result
    context = result.agent_result or AgentContext()
    metadata = context.metadata or {}
    exception = trial.result.exception_info
    reward = _reward(trial.result)
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
        checks=(
            evaluate(
                source.checks,
                text,
                completed=exception is None and finish_reason == 'stop',
            )
            if source.checks is not None
            else None
        ),
        gate_failures=_gate_failures(
            family,
            source.instruction,
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
        cost_usd=_cost(system, context),
        latency_sec=_latency(result, metadata),
        response_models=_response_models(metadata),
        request_id=metadata.get('request_id'),
    )


def _response_models(metadata: Mapping[str, Any]) -> tuple[str, ...]:
    """Return the models of a terminal agent's calls or a single-call agent's reply."""
    responses: list[Mapping[str, Any]] = metadata.get('calls', [metadata])
    return tuple(
        sorted(
            {
                model
                for response in responses
                if (model := response.get('response_model')) is not None
            }
        )
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
                case VotesSettings() | TaskTestsSettings() | CheckersSettings():
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
        case TaskTestsSettings() | CheckersSettings():
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
    metadata = context.metadata or {}
    return MeasuredUsage(
        input_tokens=context.n_input_tokens,
        cache_tokens=context.n_cache_tokens or 0,
        cache_write_tokens=metadata.get('cache_write_tokens', 0),
        output_tokens=context.n_output_tokens,
        reasoning_tokens=metadata.get('reasoning_tokens'),
        cache_write_unpriced=metadata.get('cache_write_unpriced', False),
    ).cost(system.price)
