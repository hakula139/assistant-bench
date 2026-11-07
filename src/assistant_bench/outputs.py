"""Output records of a family's trials, collected into the data directory."""

from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Literal, NamedTuple

from pydantic import BaseModel, ConfigDict, ValidationError

from assistant_bench.families import Family, Gate
from assistant_bench.messages import InputError, count_of, error_lines, series
from assistant_bench.paths import data_dir, write_atomically
from assistant_bench.scoring.checkers import Checks


type Verdict = Literal['pass', 'fail', 'ungraded']


class StopDetails(BaseModel):
    """The Messages API's account of a refusal."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    type: Literal['refusal']
    category: str | None
    """The policy area, from a set the API may extend."""

    explanation: str | None
    """Human-readable text that the API does not keep stable."""


class Output(BaseModel):
    """One trial of a family, as the arena and the reports read it."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    case: str
    system: str
    fingerprint: str | None
    job: str
    trial: str

    error: str | None
    text: str | None
    """For voted tasks, the diff of the solution the tests graded."""

    finish_reason: str | None
    """For a single-call reply, or the completion failure that ended an agent run."""

    stop_details: StopDetails | None
    """Set only when a Messages refusal ended the trial, in a reply or an agent run."""

    gate_failures: list[Gate] | None
    """An empty list admits votes, while failures settle pairs automatically.

    Absent for scorers without gates or outputs without a verdict. Rendered cases
    lack a verdict after a trial error, and tasks lack one without a test reward.
    """

    reward: float | None
    checks: Checks | None

    input_tokens: int | None
    cached_tokens: int | None
    cache_write_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    cost_usd: float | None
    latency_sec: float | None

    response_models: tuple[str, ...]
    """The distinct models the trial's responses reported, sorted."""

    request_id: str | None
    nixos_config_rev: str | None
    harness_version: str | None
    """Harbor version that wrote a task's solution, absent for single-call agents."""


def tests_pass(reward: float | None) -> bool:
    """Return whether the verifier awarded at least 1."""
    return reward is not None and reward >= 1


def system_fingerprints(outputs: Iterable[Output]) -> dict[str, str | None]:
    """Map each system with outputs to the one fingerprint they all carry.

    Raises
    ------
    InputError
        A system has outputs under several fingerprints.
    """
    by_system: defaultdict[str, set[str | None]] = defaultdict(set)
    for output in outputs:
        by_system[output.system].add(output.fingerprint)

    for system, fingerprints in sorted(by_system.items()):
        if len(fingerprints) > 1:
            raise InputError(
                f'System `{system}` has outputs under several fingerprints. Give '
                'the changed system a new name, as `docs/guide/authoring.md#systems` '
                'describes.'
            )

    return {system: fingerprints.pop() for system, fingerprints in by_system.items()}


def pool_conflicts(family: Family, outputs: Iterable[Output]) -> list[str]:
    """Return a sentence for each sign that a system's outputs pool several models.

    A system and fingerprint pools several models when its outputs report more
    than one model or, in a case family, where every sample of a case sends one
    request, when two outputs of a case differ in input tokens by more than the
    larger of 2 and 2% of the lower count. Outputs that lack the value are skipped.
    """
    pools: defaultdict[tuple[str, str], list[Output]] = defaultdict(list)
    for output in outputs:
        pools[output.system, output.fingerprint or ''].append(output)

    conflicts: list[str] = []
    for (system, _), pool in sorted(pools.items()):
        jobs: defaultdict[str, Counter[str]] = defaultdict(Counter)
        for output in pool:
            for model in output.response_models:
                jobs[model][output.job] += 1
        if len(jobs) > 1:
            models = ', '.join(
                f'`{model}` from '
                + series(
                    f'job `{job}` ({count_of(count, "output")})'
                    for job, count in sorted(counts.items())
                )
                for model, counts in sorted(jobs.items())
            )
            conflicts.append(f'System `{system}` reported several models: {models}.')

        if family.source == 'cases' and (spreads := _token_spreads(pool)):
            spread = spreads[0]
            tokens = (
                f'{spread.low:,} and {spread.high:,} input tokens in job '
                f'`{spread.low_job}`'
                if spread.low_job == spread.high_job
                else f'{spread.low:,} input tokens in job `{spread.low_job}` and '
                f'{spread.high:,} in job `{spread.high_job}`'
            )
            others = (
                f', one of {count_of(len(spreads), "case")} whose counts differ'
                if len(spreads) > 1
                else ''
            )
            conflicts.append(
                f'System `{system}` sent case `{spread.case}` with {tokens}{others}.'
            )

    return conflicts


def refuse_pool_conflicts(family: Family, outputs: Iterable[Output]) -> None:
    """Refuse a family whose outputs pool several models.

    Raises
    ------
    InputError
        `pool_conflicts` names a conflict.
    """
    conflicts = pool_conflicts(family, outputs)
    if conflicts:
        raise InputError(
            '\n'.join(
                [
                    f'Family `{family.name}` pools outputs of several models:',
                    *conflicts,
                    "Delete the minority job's trials of each system under "
                    f'`jobs/{family.name}/` in the data directory and rerun them, '
                    'or give the system a new name once its route is fixed, as '
                    '`docs/guide/running.md#model-swaps` describes.',
                ]
            )
        )


class _Spread(NamedTuple):
    case: str
    low: int
    low_job: str
    high: int
    high_job: str


def _token_spreads(pool: Iterable[Output]) -> list[_Spread]:
    """Return each case whose input tokens spread past the tolerance."""
    counts: defaultdict[str, list[tuple[int, str]]] = defaultdict(list)
    for output in pool:
        if output.input_tokens is not None:
            counts[output.case].append((output.input_tokens, output.job))

    spreads: list[_Spread] = []
    for case, values in sorted(counts.items()):
        (low, low_job), (high, high_job) = min(values), max(values)
        if high - low > max(2, 0.02 * low):
            spreads.append(_Spread(case, low, low_job, high, high_job))
    return spreads


def read_outputs(family: str) -> list[Output]:
    """Return the family's outputs, or none before its first collection.

    Raises
    ------
    InputError
        A line does not validate as an output, for example one an older
        collection wrote.
    """
    path = _outputs_file(family)
    if not path.exists():
        return []
    try:
        return [
            Output.model_validate_json(line) for line in path.read_text().splitlines()
        ]
    except ValidationError as error:
        raise InputError(
            f'{error_lines(error, f"Outputs `{path}`")}\n'
            f'Run `uv run bench collect {family}` to rewrite it.'
        ) from error


def write_outputs(family: str, outputs: list[Output]) -> None:
    write_atomically(
        _outputs_file(family),
        ''.join(f'{output.model_dump_json()}\n' for output in outputs),
    )


def _outputs_file(family: str) -> Path:
    return data_dir() / 'outputs' / f'{family}.jsonl'
