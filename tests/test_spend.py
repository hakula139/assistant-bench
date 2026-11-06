import asyncio
import shutil
from collections import Counter
from datetime import UTC, datetime, timedelta
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
    Spend,
    Tokens,
    budget_problems,
    project,
    summarize,
    update_ledger,
)
from assistant_bench.tasks import render_tasks
from support import VOTES, StubEndpoint, make_system, with_settings


START = datetime(2026, 10, 21, tzinfo=UTC)
# $3 at `make_system`'s price.
USAGE = MeasuredUsage(1_000_000, 0, 0, 100_000, None)
# A full context window and completion allowance cost $0.30.
LARGE = make_system(context_window=100_000, max_output_tokens=10_000)
UNPRICED = make_system(name='acme-small', price=None)


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


def entry(job: str = 'job-1', **fields: Any) -> Entry:
    return Entry.model_validate({**attempt(**fields).model_dump(), 'job': job})


def write_attempts(job_dir: Path, *attempts: Attempt) -> None:
    job_dir.mkdir(parents=True, exist_ok=True)
    with (job_dir / ATTEMPTS_FILE).open('a') as file:
        file.writelines(item.model_dump_json() + '\n' for item in attempts)


async def run_in_process(config: JobConfig) -> None:
    await (await Job.create(config)).run()


def spend_of(system: System, *entries: Entry) -> Spend:
    (spend,) = summarize(entries, {system.name: system})
    return spend


class TestUpdateLedger:
    def test_keeps_each_attempt_once_after_its_job_is_deleted(
        self, data_dir: Path
    ) -> None:
        job_dir = jobs.jobs_dir(VOTES) / 'job-1'
        write_attempts(job_dir, attempt())
        update_ledger(VOTES)
        shutil.rmtree(job_dir)
        write_attempts(jobs.jobs_dir(VOTES) / 'job-2', attempt(trial='b__1'))

        ledger = update_ledger(VOTES)

        assert [(item.job, item.trial) for item in ledger] == [
            ('job-1', 'a__1'),
            ('job-2', 'b__1'),
        ]
        assert update_ledger(VOTES) == ledger

    def test_keys_each_attempt_by_its_job_trial_and_start(self, data_dir: Path) -> None:
        later = attempt(started_at=START + timedelta(seconds=1))
        write_attempts(jobs.jobs_dir(VOTES) / 'job-1', attempt(), attempt(), later)
        ledger = data_dir / 'spend' / 'f.jsonl'
        ledger.parent.mkdir(parents=True)
        recorded = entry().model_dump_json() + '\n'
        ledger.write_text(recorded * 2)

        entries = update_ledger(VOTES)

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
        ledger = update_ledger(family)
        assert [item.trial for item in ledger] == [trials[0].name] * 2
        assert [len(item.responses) for item in ledger] == [0, 1]


class TestSummarize:
    @pytest.mark.parametrize(
        ('system', 'responses', 'expected'),
        [
            pytest.param(
                LARGE,
                (USAGE,),
                (True, 0, 3.0, 0.0, 0.0),
                id='known',
            ),
            pytest.param(
                LARGE,
                (USAGE, None),
                (True, 1, 3.0, 3.0, 0.3),
                id='invalid-usage',
            ),
            pytest.param(
                LARGE,
                (USAGE, MeasuredUsage(10, 0, 10, 10, None, cache_write_unpriced=True)),
                (True, 1, 3.0, 3.0, 0.3),
                id='unpriced-cache-write',
            ),
            pytest.param(
                UNPRICED,
                (USAGE, None),
                (False, 1, 0.0, None, None),
                id='unpriced',
            ),
        ],
    )
    def test_prices_the_known_usage_and_bounds_the_rest(
        self,
        system: System,
        responses: tuple[MeasuredUsage | None, ...],
        expected: tuple[object, ...],
    ) -> None:
        entries = [entry(system=system.name, responses=responses)]

        spend = spend_of(system, *entries)

        assert (
            spend.priced,
            spend.unknown,
            spend.known_usd,
            spend.imputed_usd,
            spend.ceiling_usd,
        ) == expected

    def test_leaves_a_system_the_catalog_lacks_unpriced(self) -> None:
        (spend,) = summarize([entry(system='retired')], {})

        assert (spend.priced, spend.tokens.input_tokens, spend.known_usd) == (
            False,
            1_000_000,
            0.0,
        )

    def test_counts_retries_into_the_tokens_per_answered_trial(self) -> None:
        entries = [
            entry(responses=(USAGE,)),
            entry(responses=(USAGE, None), started_at=START + timedelta(minutes=1)),
            entry(trial='b__1', responses=()),
        ]

        spend = spend_of(LARGE, *entries)

        assert (spend.trials, spend.answered, spend.responses, spend.measured) == (
            2,
            1,
            3,
            2,
        )
        assert spend.profile == Tokens(3_000_000, 0, 0, 300_000)


class TestProject:
    def test_estimates_from_its_own_usage_first(self) -> None:
        cheap = make_system(price=Price(input=1.0, output=1.0, cache_read=0.1))
        spend = [spend_of(cheap, entry())]

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
        reader = make_system(name='reader')
        writer = make_system(name='writer')
        spend = summarize(
            [
                entry(
                    system='reader',
                    responses=(MeasuredUsage(2_000_000, 0, 0, 0, None),),
                ),
                entry(
                    system='writer', responses=(MeasuredUsage(0, 0, 0, 200_000, None),)
                ),
            ],
            {'reader': reader, 'writer': writer},
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
        spend = [spend_of(LARGE, entry())]
        projections = project(Counter({LARGE.name: pending}), spend, [LARGE])

        assert budget_problems(family, spend, projections) == expected

    def test_refuses_a_priced_system_without_an_estimate(self) -> None:
        family = with_settings(VOTES, budget_usd=100.0)
        projections = project(Counter({LARGE.name: 1}), [], [LARGE, UNPRICED])

        assert budget_problems(family, [], projections) == [
            'System `acme-large` has 1 pending trial and no usage to estimate a '
            'cost from.'
        ]

    def test_refuses_a_priced_system_whose_spend_has_no_known_cost(self) -> None:
        family = with_settings(VOTES, budget_usd=100.0)
        spend = [
            spend_of(LARGE, entry(responses=(None,))),
            spend_of(UNPRICED, entry(system=UNPRICED.name, responses=(None,))),
        ]

        problems = budget_problems(family, spend, [])

        assert problems == [
            'System `acme-large` has 1 response without a known cost and none to '
            'impute one from.'
        ]
