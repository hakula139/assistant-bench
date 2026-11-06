"""The spend ledger of every agent attempt, and the cost projection of a run."""

from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self

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
    price: Price | None
    """The system's catalog price when the ledger added the attempt."""

    ceiling_usd: float | None
    """The cost of one response at a full context window and completion allowance,
    at `price` and its dearest input rate, `None` when unpriced."""

    @classmethod
    def of(cls, attempt: Attempt, job: str, system: System | None) -> Self:
        """Return the attempt priced as the system's catalog entry prices it now."""
        price = system.price if system else None
        ceiling = (
            (
                system.context_window * max(price.input, price.cache_write or 0)
                + system.max_output_tokens * price.output
            )
            / 1_000_000
            if system and price
            else None
        )
        return cls(**attempt.model_dump(), job=job, price=price, ceiling_usd=ceiling)


def update_ledger(family: Family, systems: Mapping[str, System]) -> list[Entry]:
    """Add each job's unrecorded attempts to the family's ledger, and return it.

    The ledger is the data directory's `spend/<family>.jsonl`, so the spend of a
    deleted trial or job stays recorded. An attempt is keyed by its job, trial,
    and start time, and the ledger holds each key once. Each added attempt keeps
    its system's current price, so a later price change or a system leaving the
    catalog leaves its cost as it was.
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
        for entry in _read_attempts(attempts, systems)
        if _key(entry) not in seen
    ]
    if added:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a') as file:
            file.writelines(entry.model_dump_json() + '\n' for entry in added)

    return entries + added


def _ledger_file(family: str) -> Path:
    return data_dir() / 'spend' / f'{family}.jsonl'


def _read_attempts(path: Path, systems: Mapping[str, System]) -> list[Entry]:
    attempts = [
        Attempt.model_validate_json(line) for line in path.read_text().splitlines()
    ]
    return [
        Entry.of(attempt, path.parent.name, systems.get(attempt.system))
        for attempt in attempts
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


type Imputation = Literal['own', 'borrowed']


@dataclass(frozen=True)
class Spend:
    """A system's ledger entries in one family, at the prices the entries keep."""

    system: str
    priced: bool
    """Whether any entry has a price. Responses of unpriced entries count no
    dollars."""

    trials: int
    answered: int
    """Trials with at least one response."""

    responses: int
    measured: int
    """Responses with a known cost, or with valid usage and no unpriced cache
    write when unpriced."""

    tokens: Tokens
    """The tokens of the measured responses."""

    known_usd: float
    imputed: int
    """Priced responses without a known cost that another cost stands in for."""

    imputed_usd: float
    imputation: Imputation | None
    """Whether the imputed cost is the system's own mean known cost per response or
    the family's costliest other usage per response at the system's prices."""

    ceiling: int
    """Priced responses without a known cost and nothing to impute from, counted
    at a full context window and completion allowance each."""

    ceiling_usd: float

    @property
    def unknown(self) -> int:
        """Return the responses without a known cost, or the unmeasured ones."""
        return self.responses - self.measured

    @property
    def spent_usd(self) -> float:
        return self.known_usd + self.imputed_usd + self.ceiling_usd

    @property
    def profile(self) -> Tokens | None:
        """Return the mean tokens per answered trial, imputing unmeasured responses."""
        if not self.measured:
            return None
        return self.tokens.scaled(self.responses / self.measured / self.answered)


def summarize(entries: Iterable[Entry]) -> list[Spend]:
    """Price each system's ledger entries at the prices they keep.

    A response has no known cost when its usage is invalid or its cache write has
    no declared price. Such a response costs the system's mean known cost per
    response, or else the family's costliest usage per response from another
    system at its own entry's price, or else its ceiling.
    """
    by_system: defaultdict[str, list[Entry]] = defaultdict(list)
    for entry in entries:
        by_system[entry.system].append(entry)

    rates = {
        name: rate
        for name, attempts in by_system.items()
        if (rate := _response_tokens(attempts)) is not None
    }
    return [
        _spend(
            name,
            attempts,
            [rate for other, rate in rates.items() if other != name],
        )
        for name, attempts in sorted(by_system.items())
    ]


def _measured(entry: Entry) -> list[MeasuredUsage]:
    price = entry.price
    return [
        usage
        for usage in entry.responses
        if usage is not None
        and (
            not usage.cache_write_unpriced
            if price is None
            else usage.cost(price) is not None
        )
    ]


def _response_tokens(attempts: list[Entry]) -> Tokens | None:
    measured = [usage for attempt in attempts for usage in _measured(attempt)]
    if not measured:
        return None
    return Tokens.total(measured).scaled(1 / len(measured))


def _spend(name: str, attempts: list[Entry], borrowed: list[Tokens]) -> Spend:
    measured = [usage for attempt in attempts for usage in _measured(attempt)]
    known = [
        cost
        for attempt in attempts
        if attempt.price is not None
        for usage in _measured(attempt)
        if (cost := usage.cost(attempt.price)) is not None
    ]
    own = sum(known) / len(known) if known else None
    imputed: list[float] = []
    ceilings: list[float] = []
    for attempt in attempts:
        if attempt.price is None or attempt.ceiling_usd is None:
            continue
        for _ in range(len(attempt.responses) - len(_measured(attempt))):
            rate = own if own is not None else _borrowed_cost(borrowed, attempt.price)
            if rate is None:
                ceilings.append(attempt.ceiling_usd)
            else:
                imputed.append(rate)

    return Spend(
        name,
        priced=any(attempt.price is not None for attempt in attempts),
        trials=len({(attempt.job, attempt.trial) for attempt in attempts}),
        answered=len(
            {(attempt.job, attempt.trial) for attempt in attempts if attempt.responses}
        ),
        responses=sum(len(attempt.responses) for attempt in attempts),
        measured=len(measured),
        tokens=Tokens.total(measured),
        known_usd=sum(known),
        imputed=len(imputed),
        imputed_usd=sum(imputed),
        imputation=('own' if own is not None else 'borrowed') if imputed else None,
        ceiling=len(ceilings),
        ceiling_usd=sum(ceilings),
    )


def _borrowed_cost(rates: list[Tokens], price: Price) -> float | None:
    costs = [cost for rate in rates if (cost := rate.cost(price)) is not None]
    return max(costs, default=None)


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
        lenders = [
            lender
            for lender in profiles
            if system.price is None or lender.cost(system.price) is not None
        ]
        if profile is not None:
            basis = 'own'
        elif lenders:
            basis, profile = 'borrowed', max(lenders, key=_rank(system.price))
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
        assert cost is not None
        return cost

    return rank


# ── Budget ──


def total_usd(spend: list[Spend], projections: list[Projection]) -> float:
    """Return the ledger's spend plus the projection's cost."""
    return sum(entry.spent_usd for entry in spend) + sum(
        projection.usd or 0 for projection in projections
    )


def budget_problems(
    family: Family, spend: list[Spend], projections: list[Projection]
) -> list[str]:
    """Return why the run would exceed the family's `budget_usd`, if it would.

    The total that `total_usd` returns must stay within the budget, and the
    refusal names each system's share counted at ceilings. Unpriced systems
    count no dollars, while a selected priced system refuses when its pending
    trials have no estimate.
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
        spent = {entry.system: entry for entry in spend if entry.priced}
        projected = {
            projection.system: usd
            for projection in projections
            if projection.priced and (usd := projection.usd)
        }
        shares = ', '.join(
            f'`{name}` {_spent_share(spent.get(name))} and '
            f'${projected.get(name, 0):.2f} projected'
            for name in sorted(spent.keys() | projected.keys())
        )
        problems.append(
            f'Family `{family.name}` would reach ${total:.2f}, over its `budget_usd` '
            f'of ${budget:.2f}: {shares}.'
        )

    return problems


def _spent_share(entry: Spend | None) -> str:
    if entry is None:
        return '$0.00 spent'
    share = f'${entry.spent_usd:.2f} spent'
    if entry.ceiling:
        share += f', ${entry.ceiling_usd:.2f} of it counted at ceilings,'
    return share
