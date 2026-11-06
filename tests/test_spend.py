import asyncio
import shutil
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from harbor.job import Job
from harbor.models.job.config import JobConfig

from assistant_bench import jobs
from assistant_bench.catalog import Price, System
from assistant_bench.families import load_cases, load_family
from assistant_bench.harnesses.usage import ATTEMPTS_FILE, Attempt, MeasuredUsage
from assistant_bench.spend import (
    Entry,
    Projection,
    Share,
    Spend,
    Tokens,
    budget_problems,
    project,
    summarize,
    update_ledger,
)
from assistant_bench.tasks import render_tasks
from support import START, VOTES, StubEndpoint, make_system, with_settings


# $3 at `make_system`'s price.
USAGE = MeasuredUsage(1_000_000, 0, 0, 100_000, None)
UNPRICED_WRITE = MeasuredUsage(10, 0, 10, 10, None, cache_write_unpriced=True)
# A full context window and completion allowance cost $0.35 at the $2.50 write rate.
LARGE = make_system(context_window=100_000, max_output_tokens=10_000)
NO_WRITE_PRICE = make_system(price=Price(input=2.0, output=10.0, cache_read=0.2))
UNPRICED = make_system(name='acme-small', price=None)
SYSTEMS = {LARGE.name: LARGE}


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
    return Entry.of(attempt(**fields), job, priced_as)


def write_attempts(job_dir: Path, *attempts: Attempt) -> None:
    job_dir.mkdir(parents=True, exist_ok=True)
    with (job_dir / ATTEMPTS_FILE).open('a') as file:
        file.writelines(item.model_dump_json() + '\n' for item in attempts)


async def run_in_process(config: JobConfig) -> None:
    await (await Job.create(config)).run()


def spend_of(*entries: Entry) -> Spend:
    (spend,) = summarize(entries)
    return spend


class TestUpdateLedger:
    def test_keeps_each_attempt_once_after_its_job_is_deleted(
        self, data_dir: Path
    ) -> None:
        job_dir = jobs.jobs_dir(VOTES) / 'job-1'
        write_attempts(job_dir, attempt())
        update_ledger(VOTES, SYSTEMS)
        shutil.rmtree(job_dir)
        write_attempts(jobs.jobs_dir(VOTES) / 'job-2', attempt(trial='b__1'))

        ledger = update_ledger(VOTES, SYSTEMS)

        assert [(item.job, item.trial) for item in ledger] == [
            ('job-1', 'a__1'),
            ('job-2', 'b__1'),
        ]
        assert update_ledger(VOTES, SYSTEMS) == ledger

    def test_keys_each_attempt_by_its_job_trial_and_start(self, data_dir: Path) -> None:
        later = attempt(started_at=START + timedelta(seconds=1))
        write_attempts(jobs.jobs_dir(VOTES) / 'job-1', attempt(), attempt(), later)
        ledger = data_dir / 'spend' / 'f.jsonl'
        ledger.parent.mkdir(parents=True)
        recorded = entry().model_dump_json() + '\n'
        ledger.write_text(recorded * 2)

        entries = update_ledger(VOTES, SYSTEMS)

        assert [item.started_at for item in entries] == [START, later.started_at]

    def test_keeps_the_attempt_harbor_retried(
        self, endpoint: StubEndpoint, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name, value in endpoint.env.items():
            monkeypatch.setenv(name, value)
        family = load_family('smoke-text')
        endpoint.statuses = [500]
        config = jobs.job(
            family, [make_system()], render_tasks(family, load_cases(family)[:1])
        )

        asyncio.run(run_in_process(config))

        trials = [path for path in jobs.job_path(config).iterdir() if path.is_dir()]
        assert len(trials) == 1
        ledger = update_ledger(family, {})
        assert [item.trial for item in ledger] == [trials[0].name] * 2
        assert [len(item.responses) for item in ledger] == [0, 1]


class TestSummarize:
    @pytest.mark.parametrize(
        ('system', 'responses', 'expected'),
        [
            pytest.param(LARGE, (USAGE,), (True, 0, 3.0, 0.0, 0.0), id='known'),
            pytest.param(
                LARGE, (USAGE, None), (True, 1, 3.0, 3.0, 0.0), id='invalid-usage'
            ),
            pytest.param(
                LARGE,
                (USAGE, UNPRICED_WRITE),
                (True, 1, 3.0, 3.0, 0.0),
                id='unpriced-cache-write',
            ),
            pytest.param(
                NO_WRITE_PRICE,
                (USAGE, MeasuredUsage(10, 0, 10, 10, None)),
                (True, 1, 3.0, 3.0, 0.0),
                id='undeclared-cache-write-price',
            ),
            pytest.param(LARGE, (None, None), (True, 2, 0.0, 0.0, 0.7), id='ceiling'),
            pytest.param(
                UNPRICED, (USAGE, None), (False, 1, 0.0, 0.0, 0.0), id='unpriced'
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
            sum(share.usd for share in spend.imputed.values()),
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

    def test_imputes_its_own_usage_at_the_price_of_each_entry(self) -> None:
        dearer = make_system(price=Price(input=20.0, output=100.0, cache_read=2.0))
        entries = [
            entry(responses=(USAGE,)),
            entry(
                priced_as=dearer,
                responses=(None,),
                started_at=START + timedelta(minutes=1),
            ),
        ]

        spend = spend_of(*entries)

        assert spend.imputed == {'own': Share(1, 30.0)}

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

    @pytest.mark.parametrize(
        'later',
        [
            {},
            {LARGE.name: make_system(price=Price(input=0.2, output=1.0, cache_read=0))},
        ],
        ids=['retired', 'repriced'],
    )
    def test_keeps_the_price_each_attempt_was_recorded_at(
        self, data_dir: Path, later: dict[str, System]
    ) -> None:
        write_attempts(jobs.jobs_dir(VOTES) / 'job-1', attempt())
        update_ledger(VOTES, SYSTEMS)

        (spend,) = summarize(update_ledger(VOTES, later))

        assert (spend.priced, spend.known_usd) == (True, 3.0)

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

    def test_has_no_estimate_without_usage(self) -> None:
        (projection,) = project(Counter({LARGE.name: 3}), [], [LARGE])

        assert (projection.basis, projection.tokens, projection.usd) == (
            None,
            None,
            None,
        )


class TestBudgetProblems:
    @pytest.mark.parametrize(
        ('budget', 'pending', 'expected'),
        [
            pytest.param(None, 100, [], id='no-budget'),
            pytest.param(10.0, 2, [], id='within'),
            pytest.param(9.0, 2, [], id='exactly-met'),
            pytest.param(
                5.0,
                2,
                [
                    'Family `f` would reach $9.00, over its `budget_usd` of $5.00: '
                    '`acme-large` $3.00 spent and $6.00 projected.'
                ],
                id='over',
            ),
        ],
    )
    def test_adds_the_projection_to_the_ledger(
        self, budget: float | None, pending: int, expected: list[str]
    ) -> None:
        family = with_settings(VOTES, budget_usd=budget)
        spend = [spend_of(entry())]
        projections = project(Counter({LARGE.name: pending}), spend, [LARGE])

        assert budget_problems(family, spend, projections) == expected

    def test_names_the_share_counted_at_ceilings(self) -> None:
        family = with_settings(VOTES, budget_usd=0.5)
        spend = [spend_of(entry(responses=(None, None)))]

        problems = budget_problems(family, spend, [])

        assert problems == [
            'Family `f` would reach $0.70, over its `budget_usd` of $0.50: '
            '`acme-large` $0.70 spent, $0.70 of it counted at ceilings, and $0.00 '
            'projected.'
        ]

    @pytest.mark.parametrize(
        ('budget', 'expected'),
        [
            pytest.param(3.5, [], id='under-budget'),
            pytest.param(
                3.0,
                [
                    'System `new` has 1 pending trial and no usage to estimate a cost '
                    'from, and family `f` has already reached its `budget_usd` of '
                    '$3.00.'
                ],
                id='budget-reached',
            ),
        ],
    )
    def test_runs_a_system_without_an_estimate_until_the_budget_is_reached(
        self, budget: float, expected: list[str]
    ) -> None:
        family = with_settings(VOTES, budget_usd=budget)
        new = make_system(name='new')
        spend = [spend_of(entry(responses=(USAGE,)))]
        projections = project(Counter({new.name: 1}), [], [new, UNPRICED])

        assert budget_problems(family, spend, projections) == expected
