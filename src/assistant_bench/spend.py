"""The spend ledger of every agent attempt, and the cost projection of a run."""

import json
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from assistant_bench.catalog import Price, System
from assistant_bench.families import Family
from assistant_bench.harnesses.usage import ATTEMPTS_FILE, Attempt, MeasuredUsage
from assistant_bench.jobs import jobs_dir
from assistant_bench.messages import count_of
from assistant_bench.paths import data_dir


# ── Ledger ──


class Entry(Attempt):
    """An attempt the ledger keeps after its job directory is gone."""

    job: str


def update_ledger(family: Family) -> list[Entry]:
    """Add each job's unrecorded attempts to the family's ledger, and return it.

    The ledger is the data directory's `spend/<family>.jsonl`, so the spend of a
    deleted trial or job stays recorded. An attempt is keyed by its job, trial,
    and start time, and the ledger holds each key once.
    """
    path = _ledger_file(family.name)
    entries = _unique(
        [Entry.model_validate_json(line) for line in path.read_text().splitlines()]
        if path.exists()
        else []
    )
    seen = {_key(entry) for entry in entries}
    added = [
        entry
        for attempts in sorted(jobs_dir(family).glob(f'*/{ATTEMPTS_FILE}'))
        for entry in _read_attempts(attempts)
        if _key(entry) not in seen
    ]
    if added:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a') as file:
            file.writelines(entry.model_dump_json() + '\n' for entry in added)

    return entries + added


def _ledger_file(family: str) -> Path:
    return data_dir() / 'spend' / f'{family}.jsonl'


def _read_attempts(path: Path) -> list[Entry]:
    return [
        Entry.model_validate({**json.loads(line), 'job': path.parent.name})
        for line in path.read_text().splitlines()
    ]


def _unique(entries: Iterable[Entry]) -> list[Entry]:
    return list({_key(entry): entry for entry in entries}.values())


def _key(entry: Entry) -> tuple[str, str, str]:
    return entry.job, entry.trial, entry.started_at.isoformat()


# ── Summary ──


@dataclass(frozen=True)
class Tokens:
    """Token counts named as `MeasuredUsage` names them, which may be fractional."""

    input_tokens: float
    cache_tokens: float
    cache_write_tokens: float
    output_tokens: float

    @classmethod
    def total(cls, usages: Iterable[MeasuredUsage]) -> Tokens:
        measured = list(usages)
        return cls(
            sum(usage.input_tokens for usage in measured),
            sum(usage.cache_tokens for usage in measured),
            sum(usage.cache_write_tokens for usage in measured),
            sum(usage.output_tokens for usage in measured),
        )

    def scaled(self, factor: float) -> Tokens:
        return Tokens(
            self.input_tokens * factor,
            self.cache_tokens * factor,
            self.cache_write_tokens * factor,
            self.output_tokens * factor,
        )

    def cost(self, price: Price) -> float | None:
        return price.cost(
            input_tokens=self.input_tokens,
            cached_tokens=self.cache_tokens,
            output_tokens=self.output_tokens,
            cache_write_tokens=self.cache_write_tokens,
        )


@dataclass(frozen=True)
class Spend:
    """A system's ledger entries in one family, at current catalog prices."""

    system: str
    priced: bool
    trials: int
    answered: int
    """Trials with at least one response."""

    responses: int
    measured: int
    """Responses with valid usage and no unpriced cache write."""

    tokens: Tokens
    """The tokens of the measured responses."""

    unknown: int
    """Responses without a known cost, or unmeasured ones when unpriced."""

    known_usd: float
    imputed_usd: float | None
    """The unknown responses at the mean known cost per response, `None` without
    a known cost to impute from."""

    ceiling_usd: float | None
    """The unknown responses at a full context window and completion allowance
    each, `None` when unpriced."""

    @property
    def spent_usd(self) -> float:
        """Return the known cost plus the unknown responses' imputed cost or ceiling."""
        imputed = self.imputed_usd if self.imputed_usd is not None else self.ceiling_usd
        return self.known_usd + (imputed or 0)

    @property
    def profile(self) -> Tokens | None:
        """Return the mean tokens per answered trial, imputing unmeasured responses."""
        if not self.measured:
            return None
        return self.tokens.scaled(self.responses / self.measured / self.answered)


def summarize(entries: Iterable[Entry], systems: Mapping[str, System]) -> list[Spend]:
    """Price each system's ledger entries.

    A system the catalog no longer has, or whose channel has no price, is
    unpriced. A response whose usage is invalid, or whose cache write has no
    declared price, is unmeasured and has no known cost.
    """
    by_system: defaultdict[str, list[Entry]] = defaultdict(list)
    for entry in entries:
        by_system[entry.system].append(entry)

    return [
        _spend(name, attempts, systems.get(name))
        for name, attempts in sorted(by_system.items())
    ]


def _spend(name: str, attempts: list[Entry], system: System | None) -> Spend:
    responses = [response for attempt in attempts for response in attempt.responses]
    measured = [
        response
        for response in responses
        if response is not None and not response.cache_write_unpriced
    ]
    trials = len({(attempt.job, attempt.trial) for attempt in attempts})
    answered = len(
        {(attempt.job, attempt.trial) for attempt in attempts if attempt.responses}
    )
    tokens = Tokens.total(measured)
    price = system.price if system else None
    if system is None or price is None:
        return Spend(
            name,
            priced=False,
            trials=trials,
            answered=answered,
            responses=len(responses),
            measured=len(measured),
            tokens=tokens,
            unknown=len(responses) - len(measured),
            known_usd=0.0,
            imputed_usd=None,
            ceiling_usd=None,
        )

    known = [
        cost for cost in (usage.cost(price) for usage in measured) if cost is not None
    ]
    unknown = len(responses) - len(known)
    ceiling = (
        system.context_window * price.input + system.max_output_tokens * price.output
    ) / 1_000_000
    return Spend(
        name,
        priced=True,
        trials=trials,
        answered=answered,
        responses=len(responses),
        measured=len(measured),
        tokens=tokens,
        unknown=unknown,
        known_usd=sum(known),
        imputed_usd=unknown * sum(known) / len(known) if known else None,
        ceiling_usd=unknown * ceiling,
    )


# ── Projection ──


type Basis = Literal['own', 'borrowed']


@dataclass(frozen=True)
class Projection:
    """A system's pending trials and their estimated cost."""

    system: str
    priced: bool
    pending: int
    basis: Basis | None
    """Whether the estimate uses the system's own ledger entries or another
    system's tokens per trial, `None` without an estimate."""

    per_trial_usd: float | None
    tokens: Tokens | None
    """The tokens of every pending trial, `None` without an estimate."""

    @property
    def usd(self) -> float | None:
        if not self.pending:
            return 0.0
        if self.per_trial_usd is None:
            return None
        return self.pending * self.per_trial_usd


def project(
    pending: Counter[str], spend: list[Spend], systems: list[System]
) -> list[Projection]:
    """Estimate each system's pending trials from the family's ledger.

    A system with valid usage of its own uses its mean tokens per answered trial,
    so its retries enter the estimate. Any other system borrows the tokens per
    trial that cost the most at its prices, or, when unpriced, the most tokens.
    """
    own = {entry.system: entry.profile for entry in spend}
    profiles = [profile for profile in own.values() if profile is not None]
    projections: list[Projection] = []
    for system in systems:
        basis: Basis | None = None
        profile = own.get(system.name)
        if profile is not None:
            basis = 'own'
        elif profiles:
            basis, profile = 'borrowed', max(profiles, key=_rank(system.price))
        count = pending[system.name]
        projections.append(
            Projection(
                system=system.name,
                priced=system.price is not None,
                pending=count,
                basis=basis,
                per_trial_usd=profile.cost(system.price)
                if profile is not None and system.price is not None
                else None,
                tokens=profile.scaled(count) if profile is not None else None,
            )
        )

    return projections


def _rank(price: Price | None) -> Callable[[Tokens], float]:
    def rank(profile: Tokens) -> float:
        if price is None:
            return profile.input_tokens + profile.output_tokens
        cost = profile.cost(price)
        return cost if cost is not None else -1.0

    return rank


# ── Budget ──


def total_usd(spend: list[Spend], projections: list[Projection]) -> float:
    """Return the ledger's known and imputed spend plus the projection's cost."""
    return sum(entry.spent_usd for entry in spend) + sum(
        projection.usd or 0 for projection in projections
    )


def budget_problems(
    family: Family, spend: list[Spend], projections: list[Projection]
) -> list[str]:
    """Return why the run would exceed the family's `budget_usd`, if it would.

    The total that `total_usd` returns must stay within the budget. Unpriced
    systems count no dollars, while a selected priced system refuses when its
    pending trials have no estimate.
    """
    budget = family.settings.budget_usd
    if budget is None:
        return []

    problems = [
        f'System `{projection.system}` has '
        f'{count_of(projection.pending, "pending trial")} and no usage to estimate '
        'a cost from.'
        for projection in projections
        if projection.priced and projection.usd is None
    ]
    total = total_usd(spend, projections)
    if total > budget:
        spent = {entry.system: entry.spent_usd for entry in spend if entry.priced}
        projected = {
            projection.system: usd
            for projection in projections
            if projection.priced and (usd := projection.usd)
        }
        shares = ', '.join(
            f'`{name}` ${spent.get(name, 0):.2f} spent and '
            f'${projected.get(name, 0):.2f} projected'
            for name in sorted(spent.keys() | projected.keys())
        )
        problems.append(
            f'Family `{family.name}` would reach ${total:.2f}, over its `budget_usd` '
            f'of ${budget:.2f}: {shares}.'
        )

    return problems
