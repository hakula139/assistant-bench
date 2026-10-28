import json
from collections.abc import Callable, Iterable
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, NamedTuple

import jsonschema
import numpy as np
import pytest
from pydantic import JsonValue, ValidationError

from assistant_bench import report
from assistant_bench.families import load_family
from assistant_bench.outputs import Output, write_outputs
from assistant_bench.report import (
    HumanEvidence,
    OutputMeasurement,
    Report,
    write_report,
)
from assistant_bench.scoring import ranking
from assistant_bench.scoring.pass_rates import clopper_pearson
from assistant_bench.scoring.weighting import actor_weights
from snapshot_support import (
    ROUND_NAMES,
    make_analysis,
    make_capped_analysis,
    make_evidence,
    make_round,
)
from support import (
    AT,
    EARLY_REPORT_FIXTURE,
    LEDGER_REPORT_FIXTURE,
    PROSE_DEFECTS,
    REPORT_FIXTURE,
    REPORT_SCHEMA_FIXTURE,
    TESTS_REPORT_FIXTURE,
    make_output,
    write_task_family,
)


TESTS_CATEGORIES = {
    'parse-dates': 'bug-fix',
    'retry-uploads': 'bug-fix',
    'trim-cache': 'bug-fix',
    'export-csv': 'feature',
    'page-results': 'feature',
    'tag-releases': 'feature',
}
"""Each task of the synthetic tests round, with its category."""

TESTS_PASSES = {
    'acme-pro': (2, 2, 2, 2, 2, 1),
    'acme-mini': (2, 2, 1, 2, 1, 2),
    'acme-small': (1, 0, 1, 0, 0, 0),
}
"""Each system's passing trials of two per task, in `TESTS_CATEGORIES` order."""

TESTS_USAGE = {
    'acme-pro': (0.2, 30.0),
    'acme-mini': (0.05, 12.0),
    'acme-small': (0.01, 8.0),
}


@pytest.fixture(scope='module')
def capped_results() -> report.VotesResults:
    return report.votes_results(make_capped_analysis())


def close_tests_round(families_dir: Path) -> Report:
    """Close the synthetic tests round, where two trials errored.

    Acme Small's first `retry-uploads` trial errored before its tests ran, and
    Acme Pro's second `export-csv` trial timed out after its tests passed.
    """

    def trial(system: str, task: str, index: int, passed: int) -> Output:
        untested = (system, task, index) == ('acme-small', 'retry-uploads', 0)
        timed_out = (system, task, index) == ('acme-pro', 'export-csv', 1)
        cost, latency = TESTS_USAGE[system]
        return make_output(
            case=task,
            system=system,
            fingerprint=f'{system}-fingerprint',
            trial=f'{task}-{index}',
            error='AgentTimeoutError' if untested or timed_out else None,
            reward=None if untested else float(index < passed),
            cost_usd=cost,
            latency_sec=latency + index,
        )

    write_task_family(
        families_dir,
        *TESTS_CATEGORIES,
        name='coding-tasks',
        samples=2,
        categories=TESTS_CATEGORIES,
    )
    write_outputs(
        'coding-tasks',
        [
            trial(system, task, index, passed)
            for system, passes in TESTS_PASSES.items()
            for task, passed in zip(TESTS_CATEGORIES, passes, strict=True)
            for index in range(2)
        ],
    )

    return report.close(
        load_family('coding-tasks'),
        AT,
        samples=2,
        system_names={
            'acme-pro': 'Acme Pro',
            'acme-mini': 'Acme Mini',
            'acme-small': 'Acme Small',
        },
        frozen_fingerprints={},
    )


class Tally(NamedTuple):
    votes: int
    effective_votes: float


def approx(value: JsonValue) -> object:
    """Compare the floats of a JSON document within a relative 1e-6."""
    if isinstance(value, float):
        return pytest.approx(value, rel=1e-6, abs=1e-9)
    if isinstance(value, dict):
        return {key: approx(item) for key, item in value.items()}
    if isinstance(value, list):
        return [approx(item) for item in value]
    return value


class TestReport:
    @pytest.mark.parametrize(
        ('fixture', 'sessions', 'closed_at'),
        [
            (REPORT_FIXTURE, 10, datetime(2026, 10, 21, 18, tzinfo=UTC)),
            (EARLY_REPORT_FIXTURE, 1, datetime(2026, 10, 1, 18, tzinfo=UTC)),
        ],
        ids=['full', 'early'],
    )
    def test_matches_the_committed_votes_round(
        self,
        fixture: Path,
        sessions: int,
        closed_at: datetime,
        stale_fixture: Callable[[Path, str], None],
    ) -> None:
        analysis = make_round(sessions, closed_at - timedelta(hours=1))

        closed = report.close_snapshot(
            analysis,
            closed_at,
            fingerprints={system: f'fp-{system}' for system in ROUND_NAMES},
            system_names=ROUND_NAMES,
            frozen_fingerprints={},
        )

        written = closed.model_dump_json(indent=2) + '\n'
        if not fixture.exists() or json.loads(fixture.read_text()) != approx(
            json.loads(written)
        ):
            stale_fixture(fixture, written)

    @pytest.mark.parametrize(
        'fixture',
        [REPORT_FIXTURE, EARLY_REPORT_FIXTURE, LEDGER_REPORT_FIXTURE],
        ids=['full', 'early', 'ledger'],
    )
    def test_committed_votes_rounds_account_for_every_vote(self, fixture: Path) -> None:
        results = Report.model_validate_json(fixture.read_text()).results
        assert isinstance(results, report.VotesResults)

        def tally(rows: Iterable[Tally]) -> Tally:
            counted = list(rows)
            return Tally(
                sum(row.votes for row in counted),
                sum(row.effective_votes for row in counted),
            )

        def within(row: Tally, total: Tally) -> bool:
            slack = 1e-9 * max(1.0, total.effective_votes)
            return (
                row.votes <= total.votes
                and row.effective_votes <= total.effective_votes + slack
            )

        family = Tally(results.votes, results.effective_votes)
        boards = results.categories
        standings = [Tally(row.votes, row.effective_votes) for row in results.standings]
        heads = [Tally(row.votes, row.effective_votes) for row in results.head_to_head]
        categories = [Tally(row.votes, row.effective_votes) for row in boards]
        assert all(
            within(row, Tally(row.votes, row.votes))
            for row in [family, *standings, *heads, *categories]
        )
        assert all(within(row, family) for row in [*standings, *heads, *categories])
        assert all(
            within(Tally(row.votes, row.effective_votes), category)
            for board, category in zip(boards, categories, strict=True)
            for row in board.standings
        )
        assert len({board.category for board in boards}) == len(boards)
        assert within(tally(categories), family)
        assert within(tally(standings), tally([family, family]))

        pairs = {(row.system, row.opponent): row for row in results.head_to_head}
        assert len(pairs) == len(results.head_to_head)
        assert tally(heads).votes == 2 * results.votes
        assert tally(heads).effective_votes == pytest.approx(
            2 * results.effective_votes
        )
        for (system, opponent), row in pairs.items():
            mirror = pairs[opponent, system]
            assert system != opponent
            assert (row.votes, row.wins, row.losses, row.ties, row.both_bad) == (
                mirror.votes,
                mirror.losses,
                mirror.wins,
                mirror.ties,
                mirror.both_bad,
            )
            assert row.wins + row.losses + row.ties + row.both_bad == row.votes
            assert row.effective_votes == pytest.approx(mirror.effective_votes)
            assert row.win_rate + mirror.win_rate == pytest.approx(1)

    def test_requires_an_explicit_timezone_for_the_closing_timestamp(self) -> None:
        payload = json.loads(REPORT_FIXTURE.read_text())
        payload['closed_at'] = '2026-10-21T09:00:00'

        with pytest.raises(ValidationError):
            Report.model_validate(payload)

    @pytest.mark.parametrize(
        ('path', 'value'),
        [
            (('standings', 0, 'fingerprint'), 'copied-fingerprint'),
            (('standings', 0, 'interval'), {'lower': 900, 'upper': 1100}),
            (('standings', 0, 'preliminary'), True),
            (('categories', 0, 'standings', 0, 'preliminary'), False),
            (('reference',), 'unknown-reference'),
            (('anchor',), 'unknown-anchor'),
            (('anchor',), 'acme-edge'),
            (('unranked',), ['acme-large']),
            (('categories', 0, 'unranked'), ['acme-large']),
            (('head_to_head', 0, 'opponent'), 'unknown-opponent'),
            (('unranked',), ['unknown-unranked']),
            (('categories', 0, 'unranked'), ['unknown-category-system']),
            (('standings', 0, 'system'), ''),
        ],
        ids=[
            'duplicate-fingerprint',
            'interval',
            'preliminary',
            'category-preliminary',
            'reference',
            'anchor',
            'unranked-anchor',
            'duplicate-membership',
            'category-duplicate-membership',
            'opponent',
            'unranked',
            'category-unranked',
            'empty-id',
        ],
    )
    def test_rejects_inconsistent_result_contracts(
        self, path: tuple[str | int, ...], value: Any
    ) -> None:
        payload = json.loads(REPORT_FIXTURE.read_text())
        parent = payload['results']
        for key in path[:-1]:
            parent = parent[key]
        parent[path[-1]] = value

        with pytest.raises(ValidationError):
            Report.model_validate(payload)

    @pytest.mark.parametrize(
        'change', ['missing', 'extra', 'blank-label', 'duplicate-label', 'extra-field']
    )
    def test_requires_exact_public_identity_metadata(
        self,
        change: Literal[
            'missing', 'extra', 'blank-label', 'duplicate-label', 'extra-field'
        ],
    ) -> None:
        payload = json.loads(REPORT_FIXTURE.read_text())
        systems = payload['systems']
        first, second, *_ = systems
        match change:
            case 'missing':
                del systems[first]
            case 'extra':
                systems['unused'] = {'display_name': 'Unused', 'fingerprint': None}
            case 'blank-label':
                systems[first]['display_name'] = '  '
            case 'duplicate-label':
                systems[first]['display_name'] = systems[second]['display_name']
            case 'extra-field':
                systems[first]['route'] = 'private-route'

        with pytest.raises(ValidationError):
            Report.model_validate(payload)

    def test_preserves_recorded_method_parameters_and_rules(self) -> None:
        payload = json.loads(REPORT_FIXTURE.read_text())
        method = payload['results']['method']
        method.update(
            l2_penalty=0.25, points_per_logit=200, anchor_score=1234, actor_cap=50
        )
        payload['results']['rules'].update(preliminary_decisive_votes=12)

        closed = Report.model_validate(payload)

        assert closed.results.scorer == 'votes'
        assert closed.results.method == report.VotesMethod(
            l2_penalty=0.25, points_per_logit=200, anchor_score=1234, actor_cap=50
        )
        assert closed.results.rules.preliminary_decisive_votes == 12

    @pytest.mark.parametrize(
        ('path', 'value'),
        [
            (('standings',), []),
            (('standings', 0, 'pass_rate', 'upper'), 1.5),
            (('samples',), 1),
            (('standings', 0, 'pass_hat_k'), None),
            (('decision', 'leader'), 'acme-new'),
            (('decision', 'tied'), ['acme-pro']),
            (('decision', 'tied'), ['acme-mini', 'acme-mini']),
            (('categories', 0, 'standings', 0, 'system'), 'acme-new'),
            (('categories', 1, 'category'), 'bug-fix'),
        ],
        ids=[
            'no-standings',
            'interval-above-one',
            'pass-k-with-one-sample',
            'pass-k-missing',
            'leader-outside-standings',
            'leader-tied-with-itself',
            'repeated-tie',
            'category-system',
            'repeated-category',
        ],
    )
    def test_rejects_inconsistent_tests_results(
        self, path: tuple[str | int, ...], value: Any
    ) -> None:
        payload = json.loads(TESTS_REPORT_FIXTURE.read_text())
        parent = payload['results']
        for key in path[:-1]:
            parent = parent[key]
        parent[path[-1]] = value

        with pytest.raises(ValidationError):
            Report.model_validate(payload)

    @pytest.mark.parametrize(
        'fixture',
        [
            REPORT_FIXTURE,
            EARLY_REPORT_FIXTURE,
            LEDGER_REPORT_FIXTURE,
            TESTS_REPORT_FIXTURE,
        ],
        ids=['full', 'early', 'ledger', 'tests'],
    )
    def test_committed_fixture_validates_against_the_json_schema(
        self, fixture: Path
    ) -> None:
        schema = Report.model_json_schema(mode='serialization')

        jsonschema.validate(json.loads(fixture.read_text()), schema)

    def test_matches_the_committed_json_schema(
        self, stale_fixture: Callable[[Path, str], None]
    ) -> None:
        schema = Report.model_json_schema(mode='serialization')

        if json.loads(REPORT_SCHEMA_FIXTURE.read_text()) != schema:
            stale_fixture(REPORT_SCHEMA_FIXTURE, f'{json.dumps(schema, indent=2)}\n')

    def test_committed_tests_round_contains_each_rate_and_leads_with_the_top(
        self,
    ) -> None:
        closed = Report.model_validate_json(TESTS_REPORT_FIXTURE.read_text())

        assert closed.results.scorer == 'tests'
        rates = [
            row.pass_rate
            for row in closed.results.standings
            + [row for board in closed.results.categories for row in board.standings]
        ]
        assert all(rate.lower <= rate.rate <= rate.upper for rate in rates)
        top = max(row.pass_rate.rate for row in closed.results.standings)
        assert closed.results.standings[0].pass_rate.rate == top
        assert closed.results.standings[0].system == closed.results.decision.leader

    def test_matches_the_committed_tests_round(
        self, families_dir: Path, stale_fixture: Callable[[Path, str], None]
    ) -> None:
        written = f'{close_tests_round(families_dir).model_dump_json(indent=2)}\n'

        if not TESTS_REPORT_FIXTURE.exists():
            stale_fixture(TESTS_REPORT_FIXTURE, written)
            return

        comparison = json.loads(TESTS_REPORT_FIXTURE.read_text())
        results = comparison['results']
        # SciPy's beta quantiles can differ in the last bit across platforms.
        for row in results['standings'] + [
            row for board in results['categories'] for row in board['standings']
        ]:
            for bound in ('lower', 'upper'):
                row['pass_rate'][bound] = pytest.approx(
                    row['pass_rate'][bound], rel=0, abs=1e-12
                )
        if comparison != json.loads(written):
            stale_fixture(TESTS_REPORT_FIXTURE, written)


class TestClose:
    def test_normalizes_an_offset_closing_time_before_naming_the_round(self) -> None:
        write_outputs('smoke', [make_output(case='echo', system='large')])
        utc = AT.replace(hour=23)
        local = utc.astimezone(timezone(timedelta(hours=9)))

        closed = report.close(
            load_family('smoke'),
            local,
            samples=1,
            system_names={'large': 'Public large'},
            frozen_fingerprints={},
        )

        assert closed.round == '2026-10-21'
        assert closed.closed_at == utc
        assert closed.closed_at.tzinfo == UTC

    @pytest.mark.parametrize(
        ('frozen', 'refused'),
        [
            ({'large': 'fingerprint'}, False),
            ({'large': None}, False),
            ({'large': 'earlier'}, True),
        ],
        ids=['kept', 'unrecorded', 'changed'],
    )
    def test_refuses_a_system_that_left_its_frozen_fingerprint(
        self, frozen: dict[str, str | None], refused: bool
    ) -> None:
        write_outputs(
            'smoke',
            [make_output(case='echo', system='large', fingerprint='fingerprint')],
        )

        def close() -> Report:
            return report.close(
                load_family('smoke'),
                AT,
                samples=1,
                system_names={'large': 'Public large'},
                frozen_fingerprints=frozen,
            )

        if refused:
            with pytest.raises(ValueError, match='`large` changed its published'):
                close()
        else:
            assert close().systems['large'].fingerprint == 'fingerprint'

    @pytest.mark.parametrize(
        ('trials', 'message'),
        [
            (
                [{'case': 'retired', 'fingerprint': 'a'}],
                '^Family `smoke` has no outputs on its current cases\\.$',
            ),
            (
                [
                    {'trial': 'a', 'fingerprint': 'a'},
                    {'trial': 'b', 'fingerprint': 'b'},
                ],
                'System `large` has outputs under several',
            ),
        ],
        ids=['only-retired-outputs', 'several-fingerprints'],
    )
    def test_rejects_a_tests_family_it_cannot_aggregate(
        self, trials: list[dict[str, str]], message: str
    ) -> None:
        write_outputs(
            'smoke',
            [
                make_output(**{'case': 'echo', 'system': 'large'} | trial)
                for trial in trials
            ],
        )

        with pytest.raises(ValueError, match=message):
            report.close(
                load_family('smoke'),
                AT,
                samples=1,
                system_names={'large': 'Public large'},
                frozen_fingerprints={},
            )

    def test_reports_pass_rates_for_a_tests_family(self) -> None:
        trials: list[dict[str, Any]] = [
            {'trial': '1', 'reward': 1.0, 'latency_sec': 2.0},
            {'trial': '2', 'reward': 0.0, 'latency_sec': 4.0},
            {
                'trial': '3',
                'error': 'TimeoutError',
                'reward': 1.0,
                'cost_usd': 1.0,
                'latency_sec': 60.0,
            },
            {'trial': '4', 'case': 'retired', 'reward': 1.0},
        ]
        write_outputs(
            'smoke',
            [
                make_output(
                    **{'case': 'echo', 'system': 'large', 'cost_usd': 0.01} | trial
                )
                for trial in trials
            ],
        )

        closed = report.close(
            load_family('smoke'),
            AT,
            samples=1,
            system_names={'large': 'Public large'},
            frozen_fingerprints={},
        )
        results = closed.results

        assert results.scorer == 'tests'
        (standing,) = results.standings
        assert (standing.tasks, standing.trials, standing.errored) == (1, 3, 1)
        assert standing.pass_rate.rate == pytest.approx(2 / 3)
        assert standing.pass_hat_k is None
        assert standing.mean_cost_usd == pytest.approx(0.01)
        assert standing.p50_latency_sec == 3.0
        assert closed.systems['large'].display_name == 'Public large'
        assert 'fingerprint' not in standing.model_dump()

    def test_bounds_task_means_by_their_exact_interval_over_tasks(
        self, families_dir: Path
    ) -> None:
        closed = close_tests_round(families_dir)
        results = closed.results

        assert results.scorer == 'tests'
        pro, mini, small = results.standings
        assert [pro.system, mini.system, small.system] == [
            'acme-pro',
            'acme-mini',
            'acme-small',
        ]
        assert (pro.tasks, pro.trials, pro.errored, small.errored) == (6, 12, 1, 1)
        assert pro.pass_rate.rate == pytest.approx(11 / 12)
        assert (pro.pass_rate.lower, pro.pass_rate.upper) == pytest.approx(
            clopper_pearson(11 / 12, 6)
        )
        assert pro.pass_hat_k == pytest.approx(5 / 6)
        assert small.pass_rate.rate == pytest.approx(2 / 12)

    def test_ties_the_leader_with_systems_it_beats_on_too_few_tasks(
        self, families_dir: Path
    ) -> None:
        closed = close_tests_round(families_dir)

        assert closed.results.scorer == 'tests'
        assert closed.results.decision == report.TestsDecision(
            leader='acme-pro', tied=['acme-mini']
        )

    def test_rates_each_category_over_its_own_tasks(self, families_dir: Path) -> None:
        closed = close_tests_round(families_dir)

        assert closed.results.scorer == 'tests'
        rows = [
            (board.category, row)
            for board in closed.results.categories
            for row in board.standings
        ]
        assert {row.tasks for _, row in rows} == {3}
        assert {
            (category, row.system): row.pass_rate.rate for category, row in rows
        } == pytest.approx(
            {
                ('bug-fix', 'acme-pro'): 1.0,
                ('bug-fix', 'acme-mini'): 5 / 6,
                ('bug-fix', 'acme-small'): 1 / 3,
                ('feature', 'acme-pro'): 5 / 6,
                ('feature', 'acme-mini'): 5 / 6,
                ('feature', 'acme-small'): 0.0,
            }
        )

    def test_refuses_a_round_until_every_system_has_every_trial(
        self, families_dir: Path
    ) -> None:
        write_task_family(families_dir, 'a', 'b', samples=2)
        write_outputs(
            'f',
            [
                make_output(case=case, system=system, trial=trial, reward=1.0)
                for case, system, trial in [
                    ('a', 'large', '1'),
                    ('a', 'large', '2'),
                    ('b', 'large', '1'),
                    ('a', 'small', '1'),
                    ('a', 'small', '2'),
                    ('b', 'small', '1'),
                    ('b', 'small', '2'),
                ]
            ],
        )

        with pytest.raises(
            ValueError,
            match=r'lack some: `large` on `b`\. Run `uv run bench run --system <name>`',
        ):
            report.close(
                load_family('f'),
                AT,
                samples=2,
                system_names={'large': 'Public large', 'small': 'Public small'},
                frozen_fingerprints={},
            )


class TestVotesResults:
    def test_counts_defects_on_the_output_side_and_ignores_errored_measurements(
        self,
    ) -> None:
        rows = (
            replace(
                make_evidence(0, choice='both_bad'),
                defects_a=PROSE_DEFECTS,
                defects_b=('tone',),
            ),
            replace(make_evidence(1, systems=('b', 'a')), defects_b=('facts',)),
        )
        analysis = replace(make_analysis(rows), defects=PROSE_DEFECTS)
        outputs = tuple(
            OutputMeasurement(
                case=f'case-{index}',
                system=system,
                errored=index == 1 and system == 'a',
                verdict='fail' if index == 1 else 'pass',
                reward=None,
                cost_usd=100.0 if index == 1 else 0.01,
                latency_sec=100.0 if index == 1 else 2.0,
                output_tokens=100 if index == 1 else 20,
            )
            for index in range(2)
            for system in ('a', 'b')
        )

        results = report.votes_results(replace(analysis, outputs=outputs))

        standings = {row.system: row for row in results.standings}
        assert standings['a'].defects == {
            defect: 2 if defect == 'facts' else 1 for defect in PROSE_DEFECTS
        }
        assert standings['b'].defects == {
            defect: 1 if defect == 'tone' else 0 for defect in PROSE_DEFECTS
        }
        assert (
            report.VotesResults.model_validate_json(results.model_dump_json())
            == results
        )
        assert standings['a'].both_bad_rate == 0.5
        assert standings['a'].outputs == 1
        assert standings['a'].gate_failure_rate == 0.5
        assert standings['a'].mean_cost_usd == 0.01
        assert standings['a'].p50_latency_sec == 2.0
        assert standings['a'].output_tokens is not None
        assert standings['a'].output_tokens.p50 == 20.0
        assert standings['b'].mean_cost_usd == pytest.approx(50.005)
        assert standings['a'].pass_rate is None

    def test_rates_test_passes_per_task_for_systems_with_rewards(self) -> None:
        analysis = make_analysis((make_evidence(0), make_evidence(1)))
        trials = [
            ('case-0', 'a', False, 1.0),
            ('case-0', 'a', False, 0.0),
            ('case-0', 'a', True, None),
            ('case-1', 'a', True, 1.0),
            ('case-0', 'b', True, None),
            ('case-1', 'b', True, None),
        ]
        outputs = tuple(
            OutputMeasurement(
                case=case,
                system=system,
                errored=errored,
                verdict='ungraded'
                if reward is None
                else 'pass'
                if reward >= 1
                else 'fail',
                reward=reward,
                cost_usd=None,
                latency_sec=None,
                output_tokens=None,
            )
            for case, system, errored, reward in trials
        )

        results = report.votes_results(replace(analysis, outputs=outputs))

        standings = {row.system: row for row in results.standings}
        rated = standings['a'].pass_rate
        assert rated is not None
        assert rated.rate == pytest.approx((1 / 3 + 1) / 2)
        assert (rated.lower, rated.upper) == pytest.approx(
            clopper_pearson(rated.rate, 2)
        )
        assert standings['b'].pass_rate is None

    def test_settlement_only_results_rank_without_inventing_human_diagnostics(
        self,
    ) -> None:
        analysis = make_analysis(
            settlements=(ranking.Comparison('a', 'b', 1.0, 'case', 0.25, sided=False),)
        )

        results = report.votes_results(analysis)

        assert (results.votes, results.effective_votes, results.sessions) == (0, 0, 0)
        assert results.anchor == 'a'
        assert results.standings[0].system == 'a'
        assert results.head_to_head == []
        assert results.position is results.length is results.split_half_tau is None
        assert all(row.votes == row.decisive_votes == 0 for row in results.standings)

    def test_falls_back_to_the_connected_root_when_the_reference_does_not_rank(
        self,
    ) -> None:
        analysis = make_analysis((make_evidence(0, systems=('x', 'y')),))

        results = report.votes_results(analysis)

        assert results.reference == 'a'
        assert results.anchor == 'x'
        assert (
            next(row.score for row in results.standings if row.system == 'x')
            == ranking.ANCHOR
        )
        assert results.split_half_tau is None
        assert results.length is None

    @pytest.mark.parametrize(
        'missing', [False, True], ids=['uncategorized', 'missing-key']
    )
    def test_distinguishes_uncategorized_cases_from_incomplete_frozen_inventory(
        self, missing: bool
    ) -> None:
        rows = (make_evidence(0),)
        analysis = make_analysis(
            rows, categories=() if missing else (('case-0', None),)
        )

        if missing:
            with pytest.raises(KeyError, match='case-0'):
                report.votes_results(analysis)
        else:
            results = report.votes_results(analysis)
            assert results.categories == []
            assert results.votes == 1

    def test_writes_category_boards_in_the_declared_order(self) -> None:
        rows = (make_evidence(0), make_evidence(1))
        analysis = make_analysis(
            rows, categories=(('case-0', 'reply'), ('case-1', 'docs'))
        )

        results = report.votes_results(analysis)

        assert [board.category for board in results.categories] == ['reply', 'docs']

    @pytest.mark.parametrize(
        ('scope', 'value'),
        [
            (scope, value)
            for scope in (
                'overall',
                'standing',
                'head-to-head',
                'category',
                'category-standing',
            )
            for value in (-1.0, float('inf'), float('nan'))
        ],
        ids=lambda value: str(value),
    )
    def test_rejects_invalid_effective_mass_at_every_aggregate(
        self, capped_results: report.VotesResults, scope: str, value: float
    ) -> None:
        payload = capped_results.model_dump(mode='json')
        match scope:
            case 'overall':
                target = payload
            case 'standing':
                target = payload['standings'][0]
            case 'head-to-head':
                target = payload['head_to_head'][0]
            case 'category':
                target = payload['categories'][0]
            case 'category-standing':
                target = payload['categories'][0]['standings'][0]
            case _:
                raise AssertionError(scope)

        target['effective_votes'] = value

        with pytest.raises(ValidationError):
            report.VotesResults.model_validate_json(json.dumps(payload))

    @pytest.mark.parametrize(
        'change',
        ['missing-provenance', 'unrecorded-provenance', 'negative-cutoff', 'actor-id'],
    )
    def test_requires_private_free_ledger_provenance(
        self, capped_results: report.VotesResults, change: str
    ) -> None:
        payload = capped_results.model_dump(mode='json')
        match change:
            case 'missing-provenance':
                del payload['provenance']
            case 'unrecorded-provenance':
                payload['provenance'] = {'status': 'unrecorded'}
            case 'negative-cutoff':
                payload['provenance']['receipt_cutoff'] = -1
            case 'actor-id':
                payload['provenance']['actor_id'] = 'private-actor'
            case _:
                raise AssertionError(change)

        with pytest.raises(ValidationError):
            report.VotesResults.model_validate_json(json.dumps(payload))

    def test_inherits_family_weights_in_categories_and_preference_summaries(
        self, capped_results: report.VotesResults
    ) -> None:
        results = capped_results

        parsed = report.VotesResults.model_validate_json(results.model_dump_json())

        assert parsed == results
        assert results.votes == 275
        assert results.effective_votes == pytest.approx(125)
        assert results.dropped_votes == 1
        assert results.sessions == 4
        assert results.unranked == ['x', 'y']

        standings = {row.system: row for row in results.standings}
        assert standings['a'].votes == 225
        assert standings['a'].effective_votes == pytest.approx(105)
        assert standings['a'].both_bad_rate == pytest.approx(16 / 105)
        assert standings['a'].outputs == 225
        assert standings['a'].mean_cost_usd == pytest.approx(0.01)
        assert standings['a'].p50_latency_sec == 2.0

        pair = next(
            row
            for row in results.head_to_head
            if (row.system, row.opponent) == ('a', 'b')
        )
        assert (pair.wins, pair.losses, pair.ties, pair.both_bad) == (120, 25, 40, 40)
        assert pair.effective_votes == pytest.approx(105)
        assert pair.win_rate == pytest.approx(64 / 105)
        assert pair.win_rate != pytest.approx((pair.wins + pair.ties / 2) / pair.votes)

        categories = {board.category: board for board in results.categories}
        assert set(categories) == {'docs', 'message'}
        assert categories['docs'].votes == 15
        assert categories['docs'].effective_votes == pytest.approx(9)
        assert all(
            row.effective_votes == pytest.approx(9)
            for row in categories['docs'].standings
        )

    def test_scales_length_with_inherited_weights_in_the_ranked_component(self) -> None:
        analysis = make_capped_analysis()
        connected = tuple(row for row in analysis.ordinary if row.systems == ('a', 'b'))
        differences = np.array(
            [
                (row.length_a - row.length_b) / (row.length_a + row.length_b)
                for row in connected
            ]
        )
        weights = np.array([row.comparison.weight for row in connected])
        mean = float(np.dot(differences, weights) / weights.sum())
        variance = float(np.dot((differences - mean) ** 2, weights) / weights.sum())
        feature = (differences - mean) / variance**0.5
        expected = ranking.fit(
            ranking.Matchups.build([row.comparison for row in connected]), feature
        ).covariate
        assert expected is not None

        results = report.votes_results(analysis)

        assert results.length == pytest.approx(expected * ranking.ELO_PER_LOGIT)

    def test_preserves_full_evaluation_weights_when_splitting_sessions(self) -> None:
        rows: list[HumanEvidence] = []
        actors: list[str] = []
        for session, visitor_choices in (('early', 16), ('late', 10)):
            for actor, winner, count in (
                ('owner', 'a', 76),
                ('owner', 'b', 50),
                ('visitor', 'b', visitor_choices),
            ):
                for index in range(count):
                    systems = ('a', 'b') if index % 2 else ('b', 'a')
                    rows.append(
                        make_evidence(
                            len(rows),
                            actor=actor,
                            session=session,
                            systems=systems,
                            choice='a' if systems[0] == winner else 'b',
                        )
                    )
                    actors.append(actor)

        weights = actor_weights(actors)
        ordinary = tuple(
            replace(row, comparison=row.comparison._replace(weight=weight))
            for row, weight in zip(rows, weights, strict=True)
        )
        analysis = make_analysis(ordinary)

        results = report.votes_results(analysis)

        assert results.split_half_tau == pytest.approx(-1.0)


class TestWriteReport:
    def test_writes_reports_family_round_json(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(report, 'REPORTS_DIR', tmp_path / 'reports')
        closed = Report.model_validate_json(REPORT_FIXTURE.read_text())

        path = write_report(closed)

        assert path == tmp_path / 'reports' / 'arena' / '2026-10-21.json'
        assert Report.model_validate_json(path.read_text()) == closed


class TestReportedFingerprints:
    @staticmethod
    def write_rounds(
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        early: str | None,
        late: str | None,
    ) -> Report:
        monkeypatch.setattr(report, 'REPORTS_DIR', tmp_path / 'reports')
        closed = Report.model_validate_json(REPORT_FIXTURE.read_text())
        for fixture, value in ((EARLY_REPORT_FIXTURE, early), (REPORT_FIXTURE, late)):
            written = Report.model_validate_json(fixture.read_text())
            systems = written.systems | {
                'acme-large': written.systems['acme-large'].model_copy(
                    update={'fingerprint': value}
                )
            }
            write_report(written.model_copy(update={'systems': systems}))
        return closed

    @pytest.mark.parametrize(
        ('early', 'late', 'expected'),
        [
            pytest.param('fp-a', 'fp-a', 'fp-a', id='kept'),
            pytest.param(None, 'fp-a', 'fp-a', id='recorded-later'),
            pytest.param('fp-a', None, 'fp-a', id='unrecorded-later'),
            pytest.param(None, None, None, id='never-recorded'),
        ],
    )
    def test_freezes_the_one_fingerprint_closed_reports_recorded(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        early: str | None,
        late: str | None,
        expected: str | None,
    ) -> None:
        closed = self.write_rounds(monkeypatch, tmp_path, early, late)

        frozen = report.reported_fingerprints(closed.family)

        assert frozen == {
            system: metadata.fingerprint for system, metadata in closed.systems.items()
        } | {'acme-large': expected}

    def test_rejects_reports_that_froze_two_fingerprints_under_one_system(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        closed = self.write_rounds(monkeypatch, tmp_path, 'fp-a', 'fp-b')

        with pytest.raises(ValueError, match='`acme-large` reuses an identifier'):
            report.reported_fingerprints(closed.family)

    def test_rejects_an_invalid_family_before_reading_any_report(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(report, 'REPORTS_DIR', tmp_path / 'missing')

        with pytest.raises(ValidationError):
            report.reported_fingerprints('../outside')

    @pytest.mark.parametrize('invalid', ['family', 'round', 'payload', 'timezone'])
    def test_rejects_invalid_history_before_using_its_metadata(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid: str
    ) -> None:
        monkeypatch.setattr(report, 'REPORTS_DIR', tmp_path / 'reports')
        closed = Report.model_validate_json(REPORT_FIXTURE.read_text())
        path = write_report(closed)
        payload = json.loads(path.read_text())
        if invalid == 'payload':
            del payload['systems']
        elif invalid == 'timezone':
            payload['closed_at'] = '2026-10-21T09:00:00'
        else:
            payload[invalid] = 'wrong'
        path.write_text(json.dumps(payload))

        with pytest.raises(ValueError):
            report.reported_fingerprints(closed.family)
