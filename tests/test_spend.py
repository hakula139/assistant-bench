from collections import Counter
from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from assistant_bench.catalog import Price, System
from assistant_bench.database import Param, Result
from assistant_bench.harnesses.accounting import RequestReceipt
from assistant_bench.harnesses.usage import Attempt, MeasuredUsage
from assistant_bench.spend import (
    Accounting,
    Basis,
    Entry,
    PendingAttempt,
    Projection,
    Share,
    Spend,
    Tokens,
    budget_problem,
    project,
    read_ledger,
    summarize,
)
from assistant_bench.state import AttemptStart, Claim, RequestAdmission, Run, State
from support import START, VOTES, make_system, private_database, with_settings


# $3 at `make_system`'s price.
USAGE = MeasuredUsage(1_000_000, 0, 0, 100_000, None)
UNPRICED_WRITE = MeasuredUsage(10, 0, 10, 10, None, cache_write_unpriced=True)
# A full context window and completion allowance cost $0.35 at the $2.50 write rate.
LARGE = make_system(context_window=100_000, max_output_tokens=10_000)
NO_WRITE_PRICE = make_system(price=Price(input=2.0, output=10.0, cache_read=0.2))
UNPRICED = make_system(name='acme-small', price=None)
NOTHING_IMPUTED: dict[Basis, Share] = {}


def attempt(
    trial: str = 'a__1',
    system: str = 'acme-large',
    responses: tuple[MeasuredUsage | None, ...] = (USAGE,),
    started_at: datetime = START,
) -> Attempt:
    return Attempt(
        trial=trial,
        system=system,
        fingerprint='0' * 12,
        started_at=started_at,
        finished_at=started_at + timedelta(seconds=1),
        responses=list(responses),
    )


def entry(job: str = 'job-1', priced_as: System | None = LARGE, **fields: Any) -> Entry:
    frozen = Accounting.of(priced_as) if priced_as is not None else None
    return Entry(
        **attempt(**fields).model_dump(),
        job=job,
        price=frozen.price if frozen is not None else None,
        ceiling_usd=frozen.ceiling_usd if frozen is not None else None,
    )


def started(
    state: State, frozen: Entry, identifier: str, *, scope: str = 'f'
) -> tuple[Claim, AttemptStart]:
    parent = Run(
        id=f'run-{identifier}',
        scope=scope,
        job=frozen.job,
        code_revision='a' * 40,
        case_revision='b' * 40,
        created_at=START.isoformat(),
    )
    owner = Claim(
        id=f'claim-{identifier}',
        run_id=parent.id,
        scope=scope,
        slot_key=identifier,
        trial=frozen.trial,
        system=frozen.system,
        fingerprint=frozen.fingerprint,
        accounting_json=Accounting(
            system=frozen.system,
            price=frozen.price,
            ceiling_usd=frozen.ceiling_usd,
        ).model_dump_json(),
        expected_source_revision=state.source_revision(scope),
        created_at=START.isoformat(),
    )
    start = AttemptStart(
        f'attempt-{identifier}',
        owner.id,
        frozen.started_at.isoformat().replace('+00:00', 'Z'),
    )
    state.create_run(parent)
    assert state.claim_attempt(owner)
    assert state.begin_attempt(start)
    return owner, start


def admitted(
    state: State,
    start: AttemptStart,
    identifier: str,
    usage: MeasuredUsage | None,
    *,
    terminal: bool = True,
) -> None:
    request = RequestAdmission(identifier, start.id, start.started_at)
    assert state.admit_request(request)
    if terminal:
        state.record_receipt(
            identifier,
            RequestReceipt(
                api='openai-chat-completions',
                outcome='completed',
                http_status=200,
                usage=usage,
            ).model_dump_json(),
        )


def spend_of(*entries: Entry | PendingAttempt) -> Spend:
    (spend,) = summarize(entries)
    return spend


class TestReadLedger:
    def test_retains_each_frozen_attempt_once_and_separates_canary_spend(
        self, private_state: State
    ) -> None:
        frozen = [entry(), entry(job='job-2', priced_as=None)]
        for index, recorded in enumerate(frozen):
            owner, start = started(private_state, recorded, str(index))
            admitted(private_state, start, f'request-{index}', USAGE)
            private_state.record_entry(owner.id, recorded.model_dump_json())
            private_state.record_entry(owner.id, recorded.model_dump_json())
        other = entry(job='probe-job')
        owner, start = started(private_state, other, 'probe', scope='canaries')
        admitted(private_state, start, 'request-probe', USAGE)
        private_state.record_entry(owner.id, other.model_dump_json())

        recorded = read_ledger('f', private_state)

        assert recorded == frozen
        assert read_ledger('f', private_state) == recorded
        assert read_ledger('canaries', private_state) == [other]
        assert spend_of(*recorded).known_usd == pytest.approx(3)

    @pytest.mark.parametrize('scope', ['f', 'canaries'])
    def test_pending_responses_keep_frozen_prices_and_unknown_usage(
        self, private_state: State, scope: str
    ) -> None:
        actual = LARGE.model_copy(update={'max_output_tokens': 2048})
        frozen = entry(priced_as=actual, responses=(USAGE, None, None))
        _, start = started(private_state, frozen, 'pending', scope=scope)
        admitted(private_state, start, 'known', USAGE)
        admitted(private_state, start, 'unknown', None)
        admitted(private_state, start, 'unfinished', None, terminal=False)

        (pending,) = read_ledger(scope, private_state)
        spend = spend_of(pending)

        assert isinstance(pending, PendingAttempt)
        assert pending.responses == [USAGE, None, None]
        assert pending.price == actual.price
        assert pending.ceiling_usd == pytest.approx(0.27048)
        assert spend.known_usd == pytest.approx(3)
        assert spend.unknown == 2
        assert private_state.entries(scope) == []

    def test_an_entry_covers_only_the_receipts_of_its_exact_attempt(
        self, private_state: State
    ) -> None:
        frozen = entry()
        owner, first = started(private_state, frozen, 'first')
        admitted(private_state, first, 'first-request', USAGE)
        second = AttemptStart(
            'attempt-second',
            owner.id,
            (START + timedelta(seconds=2)).isoformat().replace('+00:00', 'Z'),
        )
        assert private_state.begin_attempt(second)
        later_usage = MeasuredUsage(20, 0, 0, 10, None)
        admitted(private_state, second, 'second-request', later_usage)

        private_state.record_entry(owner.id, frozen.model_dump_json())
        recorded = read_ledger('f', private_state)
        spend = spend_of(*recorded)

        assert [(type(item), item.responses) for item in recorded] == [
            (Entry, [USAGE]),
            (PendingAttempt, [later_usage]),
        ]
        assert recorded[0] == frozen
        assert spend.responses == 2
        assert spend.known_usd == pytest.approx(3.00014)

    def test_a_concurrent_final_entry_neither_drops_nor_duplicates_pending_spend(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / 'state.sqlite'
        with private_database(path) as database, private_database(path) as other:
            original = State(database)
            writer = State(other)
            frozen = entry()
            owner, start = started(original, frozen, 'concurrent')
            admitted(original, start, 'request-concurrent', USAGE)

            class CommitEntryAfterRead:
                committed = False

                def execute(self, sql: str, params: Sequence[Param] = ()) -> Result:
                    result = database.execute(sql, params)
                    if not self.committed:
                        self.committed = True
                        writer.record_entry(owner.id, frozen.model_dump_json())
                    return result

            snapshot = read_ledger('f', State(CommitEntryAfterRead()))
            following = read_ledger('f', original)

        assert [item.responses for item in snapshot] == [[USAGE]]
        assert isinstance(snapshot[0], PendingAttempt)
        assert following == [frozen]
        assert spend_of(*snapshot).known_usd == pytest.approx(3)
        assert spend_of(*following).known_usd == pytest.approx(3)
        assert spend_of(*snapshot).responses == spend_of(*following).responses == 1


class TestSummarize:
    @pytest.mark.parametrize(
        ('system', 'responses', 'expected'),
        [
            pytest.param(
                LARGE, (USAGE,), (True, 0, 3.0, NOTHING_IMPUTED, 0.0), id='known'
            ),
            pytest.param(
                LARGE,
                (USAGE, None),
                (True, 1, 3.0, {'own': Share(1, 3.0)}, 0.0),
                id='invalid-usage',
            ),
            pytest.param(
                LARGE,
                (USAGE, UNPRICED_WRITE),
                (True, 1, 3.0, {'own': Share(1, 3.0)}, 0.0),
                id='unpriced-cache-write',
            ),
            pytest.param(
                NO_WRITE_PRICE,
                (USAGE, MeasuredUsage(10, 0, 10, 10, None)),
                (True, 1, 3.0, {'own': Share(1, 3.0)}, 0.0),
                id='undeclared-cache-write-price',
            ),
            pytest.param(
                LARGE, (None, None), (True, 2, 0.0, NOTHING_IMPUTED, 0.7), id='ceiling'
            ),
            pytest.param(
                UNPRICED,
                (USAGE, None),
                (False, 1, 0.0, NOTHING_IMPUTED, 0.0),
                id='unpriced',
            ),
        ],
    )
    def test_prices_the_known_usage_and_imputes_the_rest(
        self,
        system: System,
        responses: tuple[MeasuredUsage | None, ...],
        expected: tuple[object, ...],
    ) -> None:
        entries = [entry(system=system.name, priced_as=system, responses=responses)]

        spend = spend_of(*entries)

        assert (
            spend.priced,
            spend.unknown,
            spend.known_usd,
            spend.imputed,
            pytest.approx(spend.ceiling.usd),
        ) == expected

    def test_imputes_from_its_own_usage_then_the_costliest_other_system(
        self,
    ) -> None:
        entries = [
            entry(
                system='cheap',
                responses=(MeasuredUsage(1_000_000, 0, 0, 0, None), None),
            ),
            entry(system='dear', responses=(USAGE,)),
            entry(system='new', responses=(None, None, None)),
        ]

        cheap, _, new = summarize(entries)

        assert cheap.imputed == {'own': Share(1, 2.0)}
        assert (new.imputed, new.ceiling) == ({'borrowed': Share(3, 9.0)}, Share())

    @pytest.mark.parametrize(
        ('usage', 'repriced', 'expected'),
        [
            pytest.param(
                USAGE,
                make_system(price=Price(input=20.0, output=100.0, cache_read=2.0)),
                Share(1, 30.0),
                id='dearer',
            ),
            pytest.param(
                MeasuredUsage(1_000_000, 0, 500_000, 100_000, None),
                NO_WRITE_PRICE,
                Share(1, 3.0),
                id='without-cache-write-price',
            ),
        ],
    )
    def test_imputes_its_own_usage_at_the_price_of_each_entry(
        self, usage: MeasuredUsage, repriced: System, expected: Share
    ) -> None:
        entries = [
            entry(responses=(usage,)),
            entry(
                priced_as=repriced,
                responses=(None,),
                started_at=START + timedelta(minutes=1),
            ),
        ]

        spend = spend_of(*entries)

        assert (spend.imputed, spend.ceiling) == ({'own': expected}, Share())

    def test_prices_a_lenders_cache_as_plain_input(self) -> None:
        cached = MeasuredUsage(1_000_000, 900_000, 100_000, 0, None)
        spend = summarize(
            [
                entry(system='lender', responses=(cached,)),
                entry(system='new', responses=(None,)),
            ]
        )

        (projection,) = project(Counter({LARGE.name: 1}), spend, [LARGE])

        assert spend[1].imputed == {'borrowed': Share(1, 2.0)}
        assert (projection.basis, projection.usd) == ('borrowed', 2.0)

    def test_counts_retries_into_the_tokens_per_answered_trial(self) -> None:
        entries = [
            entry(responses=(USAGE,)),
            entry(
                responses=(USAGE, None, UNPRICED_WRITE),
                started_at=START + timedelta(minutes=1),
            ),
            entry(trial='b__1', responses=()),
        ]

        spend = spend_of(*entries)

        assert (spend.trials, spend.answered, spend.responses, spend.measured) == (
            2,
            1,
            4,
            2,
        )
        assert spend.profile == Tokens(4_000_000, 0, 0, 400_000)


class TestProject:
    def test_estimates_from_its_own_usage_first(self) -> None:
        cheap = make_system(price=Price(input=1.0, output=1.0, cache_read=0.1))
        spend = [spend_of(entry(priced_as=cheap))]

        (projection,) = project(Counter({cheap.name: 2}), spend, [cheap])

        assert projection == Projection(
            system=cheap.name,
            priced=True,
            pending=2,
            basis='own',
            per_trial_usd=1.1,
            tokens=Tokens(2_000_000, 0, 0, 200_000),
        )

    @pytest.mark.parametrize(
        ('price', 'expected'),
        [
            pytest.param(
                Price(input=1.0, output=100.0, cache_read=0.1), 20.0, id='output-heavy'
            ),
            pytest.param(
                Price(input=10.0, output=1.0, cache_read=0.1), 20.0, id='input-heavy'
            ),
            pytest.param(None, None, id='unpriced'),
        ],
    )
    def test_borrows_the_costliest_usage_at_its_own_prices(
        self, price: Price | None, expected: float | None
    ) -> None:
        spend = summarize(
            [
                entry(
                    system='reader',
                    responses=(MeasuredUsage(2_000_000, 0, 0, 0, None),),
                ),
                entry(
                    system='writer', responses=(MeasuredUsage(0, 0, 0, 200_000, None),)
                ),
            ]
        )
        target = make_system(name='new', price=price)

        (projection,) = project(Counter(new=1), spend, [target])

        assert (projection.basis, projection.usd) == ('borrowed', expected)
        assert projection.tokens is not None

    @pytest.mark.parametrize(
        ('usage', 'priced_as', 'expected'),
        [
            pytest.param(
                MeasuredUsage(1_000_000, 900_000, 0, 100_000, None),
                LARGE,
                2.76,
                id='cache-rates',
            ),
            pytest.param(
                MeasuredUsage(1_000_000, 0, 500_000, 100_000, None),
                NO_WRITE_PRICE,
                6.0,
                id='plain-input-without-cache-write-price',
            ),
        ],
    )
    def test_projects_its_own_usage_at_its_current_price(
        self, usage: MeasuredUsage, priced_as: System, expected: float
    ) -> None:
        spend = [spend_of(entry(responses=(usage,)))]

        (projection,) = project(Counter({LARGE.name: 2}), spend, [priced_as])

        assert (projection.basis, projection.usd) == ('own', expected)

    def test_has_no_estimate_without_usage(self) -> None:
        (projection,) = project(Counter({LARGE.name: 3}), [], [LARGE])

        assert (projection.basis, projection.tokens, projection.usd) == (
            None,
            None,
            None,
        )


class TestBudgetProblem:
    @pytest.mark.parametrize(
        ('budget', 'responses', 'pending', 'expected'),
        [
            pytest.param(None, (USAGE,), {LARGE.name: 100}, None, id='no-budget'),
            pytest.param(10.0, (USAGE,), {LARGE.name: 2}, None, id='within'),
            pytest.param(9.0, (USAGE,), {LARGE.name: 2}, None, id='exactly-met'),
            pytest.param(
                5.0,
                (USAGE,),
                {LARGE.name: 2},
                'Family `f` would reach $9.00, over its `budget_usd` of $5.00: '
                '`acme-large` $3.00 spent and $6.00 projected.',
                id='over',
            ),
            pytest.param(
                2.0,
                (USAGE,),
                {LARGE.name: 0, UNPRICED.name: 3},
                None,
                id='over-with-unpriced-pending',
            ),
            pytest.param(2.0, (USAGE,), {}, None, id='over-with-nothing-pending'),
            pytest.param(0.5, (None, None), {LARGE.name: 1}, None, id='ceiling-only'),
        ],
    )
    def test_refuses_only_priced_trials_that_take_the_spend_past_the_budget(
        self,
        budget: float | None,
        responses: tuple[MeasuredUsage | None, ...],
        pending: dict[str, int],
        expected: str | None,
    ) -> None:
        family = with_settings(VOTES, budget_usd=budget)
        spend = [spend_of(entry(responses=responses))]
        projections = project(Counter(pending), spend, [LARGE, UNPRICED])

        problem = budget_problem(family, spend, projections)

        assert problem == expected
