"""The spend ledger of every agent attempt, and the cost projection of a run."""

from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Literal, Self, cast

from pydantic import BaseModel, ConfigDict

from assistant_bench.catalog import Price, System
from assistant_bench.families import Family
from assistant_bench.harnesses.accounting import RequestReceipt
from assistant_bench.harnesses.usage import Attempt, MeasuredUsage
from assistant_bench.state import State


# ── Ledger ──


class Entry(Attempt):
    """An attempt the ledger keeps after its job directory is gone."""

    job: str
    price: Price | None
    """The price frozen when the attempt entered the ledger or its probe started."""

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


class Accounting(BaseModel):
    """The price and response ceiling frozen before a paid attempt is admitted."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    system: str
    price: Price | None
    ceiling_usd: float | None

    @classmethod
    def of(cls, system: System) -> Self:
        price = system.price
        return cls(
            system=system.name,
            price=price,
            ceiling_usd=(
                (
                    system.context_window * max(price.input, price.cache_write or 0)
                    + system.max_output_tokens * price.output
                )
                / 1_000_000
                if price is not None
                else None
            ),
        )


@dataclass(frozen=True)
class PendingAttempt:
    """Admitted responses without a final Entry, including unknown usage."""

    job: str
    trial: str
    system: str
    price: Price | None
    ceiling_usd: float | None
    responses: list[MeasuredUsage | None]


type AccountedAttempt = Entry | PendingAttempt


def read_ledger(scope: str, state: State) -> list[AccountedAttempt]:
    """Price one atomic snapshot of final entries and uncovered admissions."""
    entries: list[AccountedAttempt] = []
    pending: dict[str, PendingAttempt] = {}
    for row in state.accounting(scope):
        if row['kind'] == 'entry':
            entries.append(Entry.model_validate_json(cast(str, row['entry_json'])))
            continue
        attempt_id = cast(str, row['attempt_id'])
        if attempt_id not in pending:
            accounting = Accounting.model_validate_json(
                cast(str, row['accounting_json'])
            )
            pending[attempt_id] = PendingAttempt(
                job=cast(str, row['job']),
                trial=cast(str, row['trial']),
                system=accounting.system,
                price=accounting.price,
                ceiling_usd=accounting.ceiling_usd,
                responses=[],
            )
        receipt = row['receipt_json']
        pending[attempt_id].responses.append(
            RequestReceipt.model_validate_json(cast(str, receipt)).usage
            if receipt is not None
            else None
        )
    return entries + list(pending.values())


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

    @property
    def uncached(self) -> Tokens:
        """Return the tokens with cache reads and writes counted as plain input."""
        return Tokens(self.input_tokens, 0, 0, self.output_tokens)

    def cost(self, price: Price) -> float | None:
        return price.cost(
            input_tokens=self.input_tokens,
            cached_tokens=self.cache_tokens,
            output_tokens=self.output_tokens,
            cache_write_tokens=self.cache_write_tokens,
        )

    def estimate(self, price: Price) -> float:
        """Return the cost at the price, with cache as plain input if it cannot."""
        cost = self.cost(price)
        if cost is None:
            cost = self.uncached.cost(price)
            assert cost is not None
        return cost


type Basis = Literal['own', 'borrowed']


@dataclass(frozen=True)
class Share:
    """Responses without a known cost and the cost that stands in for them."""

    responses: int = 0
    usd: float = 0.0

    def add(self, responses: int, usd_each: float) -> Share:
        return Share(self.responses + responses, self.usd + responses * usd_each)


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
    imputed: Mapping[Basis, Share]
    """Priced responses without a known cost, at the system's own mean tokens per
    measured response or else another system's, at the entry's price."""

    ceiling: Share
    """Priced responses without a known cost and no usage to impute from, at a
    full context window and completion allowance each, which `spent_usd` leaves
    out."""

    @property
    def unknown(self) -> int:
        """Return the responses without a known cost, or the unmeasured ones."""
        return self.responses - self.measured

    @property
    def spent_usd(self) -> float:
        return self.known_usd + sum(share.usd for share in self.imputed.values())

    @property
    def profile(self) -> Tokens | None:
        """Return the mean tokens per answered trial, imputing unmeasured responses."""
        if not self.measured:
            return None
        return self.tokens.scaled(self.responses / self.measured / self.answered)


def summarize(entries: Iterable[AccountedAttempt]) -> list[Spend]:
    """Price each system's ledger entries at the prices they keep.

    A response has no known cost when its usage is invalid or its price cannot
    cost it, as when its cache write has no declared price. Such a response costs
    the system's own mean tokens per measured response at its entry's price, with
    their cache reads and writes as plain input when that price cannot cost them.
    A system without measured usage of its own borrows the costliest other
    system's, with the cache priced as plain input. With no measured usage
    anywhere, it is counted at its ceiling, which the spend leaves out until a
    later run measures usage to impute from.
    """
    by_system: defaultdict[str, list[AccountedAttempt]] = defaultdict(list)
    for entry in entries:
        by_system[entry.system].append(entry)

    rates = {name: _rate(attempts) for name, attempts in by_system.items()}
    return [
        _spend(
            name,
            attempts,
            rates[name],
            [rate for other, rate in rates.items() if other != name and rate],
        )
        for name, attempts in sorted(by_system.items())
    ]


def _measured(entry: AccountedAttempt) -> list[tuple[MeasuredUsage, float]]:
    """Return each measured response with its cost, zero when unpriced."""
    price = entry.price
    if price is None:
        return [
            (usage, 0.0)
            for usage in entry.responses
            if usage is not None and not usage.cache_write_unpriced
        ]
    return [
        (usage, cost)
        for usage in entry.responses
        if usage is not None and (cost := usage.cost(price)) is not None
    ]


def _rate(attempts: list[AccountedAttempt]) -> Tokens | None:
    """Return the mean tokens per measured response, `None` without one."""
    measured = [usage for attempt in attempts for usage, _ in _measured(attempt)]
    if not measured:
        return None
    return Tokens.total(measured).scaled(1 / len(measured))


def _spend(
    name: str,
    attempts: list[AccountedAttempt],
    own: Tokens | None,
    others: list[Tokens],
) -> Spend:
    measured: list[MeasuredUsage] = []
    known_usd = 0.0
    imputed: dict[Basis, Share] = {}
    ceiling = Share()
    for attempt in attempts:
        costs = _measured(attempt)
        measured += [usage for usage, _ in costs]
        known_usd += sum(cost for _, cost in costs)
        missing = len(attempt.responses) - len(costs)
        if not missing or attempt.price is None or attempt.ceiling_usd is None:
            continue
        match _imputed(own, others, attempt.price):
            case (basis, cost):
                imputed[basis] = imputed.get(basis, Share()).add(missing, cost)
            case None:
                ceiling = ceiling.add(missing, attempt.ceiling_usd)

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
        known_usd=known_usd,
        imputed=imputed,
        ceiling=ceiling,
    )


def _imputed(
    own: Tokens | None, others: list[Tokens], price: Price
) -> tuple[Basis, float] | None:
    """Return the basis and cost of each response without a known cost, if any."""
    if own is not None:
        return 'own', own.estimate(price)
    if others:
        return 'borrowed', max(rate.uncached.estimate(price) for rate in others)
    return None


# ── Projection ──


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
    so its retries enter the estimate, with its cache reads and writes counted as
    plain input when its current price cannot cost them. Any other system borrows
    the tokens per trial that cost the most at its prices, or, when unpriced, the
    most tokens, with the lender's cache reads and writes counted as plain input.
    """
    own = {entry.system: entry.profile for entry in spend}
    lenders = [profile.uncached for profile in own.values() if profile is not None]
    projections: list[Projection] = []
    for system in systems:
        basis: Basis | None = None
        profile = own.get(system.name)
        if profile is not None:
            basis = 'own'
        elif lenders:
            basis, profile = 'borrowed', max(lenders, key=_rank(system.price))
        per_trial_usd = (
            profile.estimate(system.price)
            if profile is not None and system.price is not None
            else None
        )
        count = pending[system.name]
        projections.append(
            Projection(
                system=system.name,
                priced=system.price is not None,
                pending=count,
                basis=basis,
                per_trial_usd=per_trial_usd,
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
    """Return the ledger's spend plus the projections that have an estimate."""
    return sum(entry.spent_usd for entry in spend) + sum(
        projection.usd or 0 for projection in projections
    )


def budget_problem(
    family: Family, spend: list[Spend], projections: list[Projection]
) -> str | None:
    """Return why the run would exceed the family's `budget_usd`, if it would.

    Only a run with priced pending trials can exceed it, when the total that
    `total_usd` returns is over the budget. Unpriced systems count no dollars, and
    a priced system without an estimate counts none until its usage is measured.
    """
    budget = family.settings.budget_usd
    if budget is None or not any(
        projection.priced and projection.pending for projection in projections
    ):
        return None

    total = total_usd(spend, projections)
    if total <= budget:
        return None

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
    return (
        f'Family `{family.name}` would reach ${total:.2f}, over its `budget_usd` '
        f'of ${budget:.2f}: {shares}.'
    )
