import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import jsonschema
import numpy as np
import pytest
from pydantic import ValidationError

from assistant_bench import report
from assistant_bench.arena.service import Arena, load_families
from assistant_bench.arena.store import Ballot, Vote, VoteStore
from assistant_bench.families import Family, Gate, load_family
from assistant_bench.outputs import Output, read_outputs, write_outputs
from assistant_bench.report import (
    Distribution,
    Report,
    close,
    text_length,
    write_report,
)
from assistant_bench_arena.models import Choice, Defect, Defects
from support import (
    AT,
    EARLY_REPORT_FIXTURE,
    RENDER_ORIGIN,
    REPORT_FIXTURE,
    REPORT_SCHEMA_FIXTURE,
    SYNTHETIC_CLOSE,
    SYNTHETIC_EARLY_CLOSE,
    SYNTHETIC_NAMES,
    Clock,
    make_arena,
    make_ballot,
    make_output,
    passing_outputs,
    write_synthetic_round,
    write_task_family,
    write_votes_family,
)


def approx(value: Any) -> Any:
    """Compare floats in a JSON document within a relative 1e-6."""
    if isinstance(value, float):
        return pytest.approx(value, rel=1e-6, abs=1e-9)
    if isinstance(value, dict):
        return {key: approx(item) for key, item in value.items()}
    if isinstance(value, list):
        return [approx(item) for item in value]
    return value


def write_passing_outputs(
    cases: dict[str, str], systems: list[str]
) -> dict[tuple[str, str], Output]:
    """Write family `f`'s outputs, one passing output per case and system."""
    outputs = passing_outputs(cases, systems)
    write_outputs('f', list(outputs.values()))
    return outputs


def close_round(family: Family, store: VoteStore, closed_at: datetime) -> Report:
    systems = set(family.settings.systems) | {
        output.system for output in read_outputs(family.name)
    }
    names = {system: f'Public {system}' for system in systems}
    return close(family, store, closed_at, system_names=names, previous_reports=[])


def small_family(families_dir: Path, store: VoteStore) -> Family:
    """Write family `f` with decisive, tied, and both-bad votes across categories."""
    cases = {'c1': 'reply', 'c2': 'docs', 'c3': 'misc'}
    write_votes_family(families_dir, 'f', ['large', 'medium', 'small'], cases)
    profiles: dict[str, dict[str, Any]] = {
        'large': {
            'text': 'one two three',
            'cost_usd': 0.02,
            'latency_sec': 5.0,
            'output_tokens': 10,
        },
        'medium': {
            'text': '一二三四五六',
            'cost_usd': 0.01,
            'latency_sec': 4.0,
            'output_tokens': 10,
        },
        'small': {'text': 'four five', 'latency_sec': 3.0},
    }
    outputs = [
        output.model_copy(update=profiles[output.system])
        for output in passing_outputs(cases, list(profiles)).values()
    ]
    write_outputs('f', outputs)
    by_trial = {output.trial: output for output in outputs}
    session = store.start_session('f', AT)
    votes: list[tuple[str, str, str, Choice, Defects, str | None]] = [
        (
            'c1',
            'large',
            'medium',
            'a',
            Defects(a=frozenset({'length'})),
            'Drops a fact.',
        ),
        ('c1', 'medium', 'small', 'tie', Defects(b=frozenset({'tone'})), None),
        ('c2', 'small', 'large', 'both_bad', Defects(), None),
        ('c2', 'large', 'small', 'b', Defects(), None),
        ('c3', 'medium', 'small', 'a', Defects(), None),
    ]
    for case, left, right, choice, defects, note in votes:
        ballot = make_ballot(
            by_trial[f'{case}__{left}'],
            by_trial[f'{case}__{right}'],
            session=session.id,
            choice=choice,
            defects=defects,
            note=note,
        )
        store.record(ballot, AT)
    return load_family('f')


class TestReport:
    def test_requires_an_explicit_timezone_for_the_closing_timestamp(self) -> None:
        payload = json.loads(REPORT_FIXTURE.read_text())
        payload['closed_at'] = '2026-10-21T09:00:00'

        with pytest.raises(ValidationError):
            Report.model_validate(payload)

    @pytest.mark.parametrize(
        ('path', 'value'),
        [
            (('standings', 0, 'interval'), {'lower': 900, 'upper': 1100}),
            (('standings', 0, 'best_rank'), 1),
            (('standings', 0, 'worst_rank'), 2),
            (
                ('categories', 0, 'standings', 0, 'interval'),
                {'lower': 900, 'upper': 1100},
            ),
            (('separability',), 0.5),
            (
                ('decision',),
                {'leader': 'acme-large', 'separated': True, 'tie_break': 'score'},
            ),
        ],
        ids=[
            'interval',
            'best-rank',
            'worst-rank',
            'category-interval',
            'separability',
            'decision',
        ],
    )
    def test_rejects_unsupported_inference_in_both_python_and_json_schema(
        self, path: tuple[str | int, ...], value: Any
    ) -> None:
        payload = json.loads(REPORT_FIXTURE.read_text())
        parent = payload['results']
        for key in path[:-1]:
            parent = parent[key]
        parent[path[-1]] = value

        with pytest.raises(ValidationError):
            Report.model_validate(payload)
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(payload, Report.model_json_schema(mode='serialization'))

    @pytest.mark.parametrize(
        ('path', 'value'),
        [
            (('standings', 0, 'fingerprint'), 'copied-fingerprint'),
            (('standings', 0, 'preliminary'), True),
            (('categories', 0, 'standings', 0, 'preliminary'), False),
            (('effective_votes',), 0.25),
            (('standings', 0, 'effective_votes'), 0.25),
            (('categories', 0, 'effective_votes'), 0.25),
            (('categories', 0, 'standings', 0, 'effective_votes'), 0.25),
            (('head_to_head', 0, 'effective_votes'), 0.25),
            (('reference',), 'unknown-reference'),
            (('anchor',), 'unknown-anchor'),
            (('anchor',), 'acme-edge'),
            (('unranked',), ['acme-large']),
            (('categories', 0, 'unranked'), ['acme-large']),
            (('rater_agreement', 'below_floor'), True),
            (('head_to_head', 0, 'opponent'), 'unknown-opponent'),
            (('unranked',), ['unknown-unranked']),
            (('categories', 0, 'unranked'), ['unknown-category-system']),
            (('standings', 0, 'system'), ''),
        ],
        ids=[
            'duplicate-fingerprint',
            'preliminary',
            'category-preliminary',
            'overall-mass',
            'standing-mass',
            'category-mass',
            'category-standing-mass',
            'head-to-head-mass',
            'reference',
            'anchor',
            'unranked-anchor',
            'duplicate-membership',
            'category-duplicate-membership',
            'contradictory-repeat-floor',
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
    def test_requires_exact_public_identity_metadata(self, change: str) -> None:
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
        method.update(l2_penalty=0.25, points_per_logit=200, anchor_score=1234)
        payload['results']['rules'].update(
            preliminary_decisive_votes=12, rater_agreement_floor=0.5
        )

        closed = Report.model_validate(payload)

        assert closed.results.scorer == 'votes'
        assert closed.results.method.l2_penalty == 0.25
        assert closed.results.method.points_per_logit == 200
        assert closed.results.method.anchor_score == 1234
        assert closed.results.rules.preliminary_decisive_votes == 12
        assert closed.results.rules.rater_agreement_floor == 0.5

    @pytest.mark.parametrize(
        'fixture', [REPORT_FIXTURE, EARLY_REPORT_FIXTURE], ids=['full', 'early']
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


class TestClose:
    def test_normalizes_an_offset_closing_time_before_naming_the_round(
        self, families_dir: Path, store: VoteStore
    ) -> None:
        family = small_family(families_dir, store)
        utc = AT.replace(hour=23)
        local = utc.astimezone(timezone(timedelta(hours=9)))

        closed = close_round(family, store, local)

        assert closed.round == '2026-10-21'
        assert closed.closed_at == utc
        assert closed.closed_at.tzinfo == UTC

    def test_rejects_a_naive_public_closing_time(
        self, families_dir: Path, store: VoteStore
    ) -> None:
        family = small_family(families_dir, store)

        with pytest.raises(ValueError):
            close_round(family, store, AT.replace(tzinfo=None))

    def test_homogeneous_cases_keep_points_without_claiming_population_inference(
        self, families_dir: Path, store: VoteStore
    ) -> None:
        cases = {'one': 'reply', 'two': 'reply'}
        write_votes_family(families_dir, 'f', ['a', 'b', 'c', 'd'], cases)
        outputs = write_passing_outputs(cases, ['a', 'b', 'c', 'd'])
        for i in range(20):
            session = store.start_session('f', AT + timedelta(seconds=i))
            for case in cases:
                left, right, choice = ('a', 'b', 'a') if i % 2 == 0 else ('b', 'a', 'b')
                store.record(
                    make_ballot(
                        outputs[case, left],
                        outputs[case, right],
                        session=session.id,
                        choice=choice,
                    ),
                    AT,
                )
        session = store.start_session('f', AT)
        store.record(
            make_ballot(outputs['one', 'c'], outputs['one', 'd'], session=session.id),
            AT,
        )

        closed = close_round(load_family('f'), store, AT)

        results = closed.results
        assert results.scorer == 'votes'
        assert results.inference.model_dump() == {
            'status': 'unavailable',
            'target': 'new-cases-and-voters',
            'reason': 'population-method-not-validated',
        }
        assert (results.votes, results.effective_votes, results.sessions) == (
            41,
            41,
            21,
        )
        assert results.separability is results.decision is None
        assert results.unranked == ['c', 'd']
        standings = {row.system: row for row in results.standings}
        assert standings.keys() == {'a', 'b'}
        assert standings['a'].score > standings['b'].score
        for standing in standings.values():
            assert np.isfinite(standing.score)
            assert (
                standing.interval is standing.best_rank is standing.worst_rank is None
            )
            assert (
                standing.votes,
                standing.effective_votes,
                standing.decisive_votes,
            ) == (40, 40, 40)
        (category,) = results.categories
        assert (category.votes, category.effective_votes) == (41, 41)
        assert category.unranked == ['c', 'd']
        for category_standing in category.standings:
            assert category_standing.interval is None
            assert (
                category_standing.votes,
                category_standing.effective_votes,
                category_standing.decisive_votes,
            ) == (40, 40, 40)
        assert {row.system for row in results.head_to_head} == {'a', 'b', 'c', 'd'}
        assert all(row.effective_votes == row.votes for row in results.head_to_head)
        assert closed.systems['c'].fingerprint == 'fp-c'

    @pytest.mark.parametrize(
        'prior_unranked', [False, True], ids=['ranked', 'unranked']
    )
    def test_reuses_verified_retired_identity_without_relabeling_history(
        self, families_dir: Path, store: VoteStore, prior_unranked: bool
    ) -> None:
        cases = {'one': 'reply'}
        write_votes_family(families_dir, 'f', ['a', 'b', 'c'], cases)
        outputs = write_passing_outputs(cases, ['a', 'b', 'c'])
        session = store.start_session('f', AT)
        store.record(
            make_ballot(outputs['one', 'a'], outputs['one', 'b'], session=session.id),
            AT,
        )
        if not prior_unranked:
            store.record(
                make_ballot(
                    outputs['one', 'b'], outputs['one', 'c'], session=session.id
                ),
                AT,
            )
        family = load_family('f')
        previous = close(
            family,
            store,
            AT,
            system_names={'a': 'Old A', 'b': 'Old B', 'c': 'Retired C'},
            previous_reports=[],
        )
        if prior_unranked:
            assert previous.results.scorer == 'votes'
            assert previous.results.unranked == ['c']
        frozen = previous.model_dump_json()

        current = close(
            family,
            store,
            AT + timedelta(days=1),
            system_names={'a': 'Renamed A', 'b': 'Current B'},
            previous_reports=[previous],
        )

        assert current.systems['a'].display_name == 'Renamed A'
        assert current.systems['c'].model_dump() == {
            'display_name': 'Retired C',
            'fingerprint': 'fp-c',
        }
        assert previous.model_dump_json() == frozen
        assert previous.systems['a'].display_name == 'Old A'

    @pytest.mark.parametrize(
        ('prior_fingerprint', 'current_fingerprint', 'active'),
        [
            ('different', 'fp-small', False),
            (None, 'fp-small', False),
            ('fp-small', None, False),
            (None, None, False),
            ('different', 'fp-small', True),
        ],
        ids=[
            'mismatch',
            'unknown-history',
            'unknown-current',
            'both-unknown',
            'active-id-reuse',
        ],
    )
    def test_rejects_unverified_retirement_or_reused_identity(
        self,
        families_dir: Path,
        store: VoteStore,
        prior_fingerprint: str | None,
        current_fingerprint: str | None,
        active: bool,
    ) -> None:
        family = small_family(families_dir, store)
        previous = close_round(family, store, AT)
        prior = previous.systems['small'].model_copy(
            update={'fingerprint': prior_fingerprint}
        )
        previous = previous.model_copy(
            update={'systems': previous.systems | {'small': prior}}
        )
        write_outputs(
            'f',
            [
                output.model_copy(update={'fingerprint': current_fingerprint})
                if output.system == 'small'
                else output
                for output in read_outputs('f')
            ],
        )
        names = {'large': 'Large', 'medium': 'Medium'}
        if active:
            names['small'] = 'Current Small'

        with pytest.raises(ValueError, match='small'):
            close(
                family,
                store,
                AT + timedelta(days=1),
                system_names=names,
                previous_reports=[previous],
            )

    def test_does_not_search_past_a_later_contradictory_identity(
        self, families_dir: Path, store: VoteStore
    ) -> None:
        family = small_family(families_dir, store)
        first = close_round(family, store, AT)
        changed = first.systems['small'].model_copy(update={'fingerprint': 'different'})
        later = first.model_copy(
            update={
                'closed_at': AT + timedelta(days=1),
                'round': '2026-10-22',
                'systems': first.systems | {'small': changed},
            }
        )

        with pytest.raises(ValueError, match='small'):
            close(
                family,
                store,
                AT + timedelta(days=2),
                system_names={'large': 'Large', 'medium': 'Medium'},
                previous_reports=[first, later],
            )

    @pytest.mark.parametrize('invalid', ['family', 'closing-time'])
    def test_requires_history_from_this_family_before_the_closing_boundary(
        self, families_dir: Path, store: VoteStore, invalid: str
    ) -> None:
        family = small_family(families_dir, store)
        prior = close_round(family, store, AT)
        if invalid == 'family':
            prior = prior.model_copy(update={'family': 'another-family'})
        else:
            prior = prior.model_copy(update={'closed_at': AT + timedelta(days=1)})

        with pytest.raises(ValueError, match='this family.*earlier'):
            close(
                family,
                store,
                AT + timedelta(days=1),
                system_names={'large': 'Large', 'medium': 'Medium', 'small': 'Small'},
                previous_reports=[prior],
            )

    def test_known_identity_continuity_survives_an_unknown_intermediate_round(
        self, families_dir: Path, store: VoteStore
    ) -> None:
        family = small_family(families_dir, store)
        first = close_round(family, store, AT)
        unknown = first.systems['small'].model_copy(update={'fingerprint': None})
        later = first.model_copy(
            update={
                'closed_at': AT + timedelta(days=1),
                'round': '2026-10-22',
                'systems': first.systems | {'small': unknown},
            }
        )
        write_outputs(
            'f',
            [
                output.model_copy(update={'fingerprint': 'reused'})
                if output.system == 'small'
                else output
                for output in read_outputs('f')
            ],
        )

        with pytest.raises(ValueError, match='small'):
            close(
                family,
                store,
                AT + timedelta(days=2),
                system_names={'large': 'Large', 'medium': 'Medium', 'small': 'Small'},
                previous_reports=[first, later],
            )

    @pytest.mark.parametrize(
        ('fixture', 'sessions', 'closed_at'),
        [
            (REPORT_FIXTURE, 10, SYNTHETIC_CLOSE),
            (EARLY_REPORT_FIXTURE, 1, SYNTHETIC_EARLY_CLOSE),
        ],
        ids=['full', 'early'],
    )
    def test_matches_the_committed_fixture(
        self,
        families_dir: Path,
        store: VoteStore,
        fixture: Path,
        sessions: int,
        closed_at: datetime,
        stale_fixture: Callable[[Path, str], None],
    ) -> None:
        write_synthetic_round(families_dir, store, sessions)

        closed = close(
            load_family('arena'),
            store,
            closed_at,
            system_names=SYNTHETIC_NAMES,
            previous_reports=[],
        )

        written = json.loads(closed.model_dump_json())
        if json.loads(fixture.read_text()) != approx(written):
            stale_fixture(fixture, f'{closed.model_dump_json(indent=2)}\n')

    def test_reports_aggregates_under_public_names_only(
        self, families_dir: Path, store: VoteStore
    ) -> None:
        family = small_family(families_dir, store)

        closed = close_round(family, store, AT).model_dump_json()

        for private in (
            'c1',
            'c3',
            'job-1',
            '__',
            'Write about',
            '一二三',
            'Drops a fact.',
        ):
            assert private not in closed

    def test_tallies_each_defect_against_the_output_it_tags(
        self, families_dir: Path, store: VoteStore
    ) -> None:
        family = small_family(families_dir, store)

        results = close_round(family, store, AT).results

        assert results.scorer == 'votes'
        defects = {standing.system: standing.defects for standing in results.standings}
        assert defects['large']['length'] == 1
        assert defects['medium']['length'] == 0
        assert defects['small']['tone'] == 1
        assert defects['medium']['tone'] == 0
        both_bad = {s.system: s.both_bad_rate for s in results.standings}
        assert both_bad == {'large': 1 / 3, 'medium': 0.0, 'small': 1 / 4}
        large_small = next(
            row
            for row in results.head_to_head
            if (row.system, row.opponent) == ('large', 'small')
        )
        assert (large_small.wins, large_small.losses, large_small.both_bad) == (0, 1, 1)
        assert large_small.win_rate == pytest.approx(1 / 4)
        small = next(s for s in results.standings if s.system == 'small')
        assert small.output_tokens is None
        assert small.mean_cost_usd is None

    def test_leaves_errored_trials_out_of_a_standing_s_aggregates(
        self, families_dir: Path, store: VoteStore
    ) -> None:
        family = small_family(families_dir, store)
        errored = make_output(
            trial='c1__large__2',
            job='job-1',
            case='c1',
            system='large',
            fingerprint='fp-large',
            error='TimeoutError',
            output_tokens=999,
            cost_usd=1.0,
            latency_sec=99.0,
        )
        write_outputs('f', [*read_outputs('f'), errored])

        results = close_round(family, store, AT).results

        assert results.scorer == 'votes'
        large = next(s for s in results.standings if s.system == 'large')
        assert large.outputs == 3
        assert large.gate_failure_rate == 0.0
        assert large.mean_cost_usd == pytest.approx(0.02)
        assert large.p50_latency_sec == 5.0
        assert large.latency_sec == Distribution(p10=5.0, p50=5.0, p90=5.0)
        assert large.output_tokens == Distribution(p10=10.0, p50=10.0, p90=10.0)

    def test_lets_the_results_that_gate_failures_settle_replace_votes(
        self, families_dir: Path, store: VoteStore
    ) -> None:
        family = small_family(families_dir, store)
        before = close_round(family, store, AT).results
        outputs = read_outputs('f')
        write_outputs(
            'f',
            [
                output.model_copy(update={'gate_failures': [Gate.TRUNCATED]})
                if output.system == 'small'
                else output
                for output in outputs
            ],
        )

        after = close_round(family, store, AT).results

        assert before.scorer == after.scorer == 'votes'
        small_before, small_after = (
            next(s for s in results.standings if s.system == 'small')
            for results in (before, after)
        )
        assert small_after.score < small_before.score - 100
        assert (small_before.votes, small_after.votes) == (4, 0)
        assert small_after.both_bad_rate is None
        assert small_after.gate_failure_rate == 1.0
        assert (after.votes, after.dropped_votes) == (1, 4)
        assert all('small' not in (r.system, r.opponent) for r in after.head_to_head)

    @pytest.mark.parametrize(
        ('failed', 'identical', 'winner'),
        [
            pytest.param({'b'}, False, 'a', id='one-passes'),
            pytest.param({'a', 'b'}, False, None, id='both-fail'),
            pytest.param(set(), True, None, id='identical-passing-outputs'),
        ],
    )
    @pytest.mark.parametrize('historical', [False, True], ids=['fresh', 'regraded'])
    def test_closes_fully_settled_rounds_without_counting_human_votes(
        self,
        families_dir: Path,
        store: VoteStore,
        failed: set[str],
        identical: bool,
        winner: str | None,
        historical: bool,
    ) -> None:
        cases = {f'r{i}': 'reply' for i in range(2)} | {
            f'd{i}': 'docs' for i in range(2)
        }
        write_votes_family(families_dir, 'f', ['a', 'b'], cases)
        outputs = passing_outputs(cases, ['a', 'b'])
        if historical:
            session = store.start_session('f', AT)
            for case in cases:
                original = store.record(
                    make_ballot(
                        outputs[case, 'a'], outputs[case, 'b'], session=session.id
                    ),
                    AT,
                )
                store.record(
                    make_ballot(
                        outputs[case, 'b'],
                        outputs[case, 'a'],
                        session=session.id,
                        repeat_of=original.id,
                    ),
                    AT,
                )
        write_outputs(
            'f',
            [
                output.model_copy(
                    update={
                        'gate_failures': [Gate.TRUNCATED]
                        if output.system in failed
                        else [],
                        'text': 'Same answer.' if identical else output.text,
                    }
                )
                for output in outputs.values()
            ],
        )

        results = close_round(load_family('f'), store, AT).results

        assert results.scorer == 'votes'
        assert (results.votes, results.dropped_votes, results.sessions) == (
            0,
            2 * len(cases) if historical else 0,
            0,
        )
        assert results.head_to_head == []
        assert results.split_half_tau is None
        assert results.length is None
        assert results.position is None
        assert results.decision is None
        assert results.rater_agreement.model_dump() == {
            'repeats': 0,
            'agreement': None,
            'below_floor': False,
        }
        standings = {standing.system: standing for standing in results.standings}
        assert standings.keys() == {'a', 'b'}
        assert results.unranked == []
        for standing in standings.values():
            assert (
                standing.votes,
                standing.effective_votes,
                standing.decisive_votes,
            ) == (0, 0, 0)
            assert standing.both_bad_rate is None
            assert not any(standing.defects.values())
        if winner is None:
            assert standings['a'].score == standings['b'].score == 1000
        else:
            assert standings[winner].score > standings['b'].score
        assert [board.category for board in results.categories] == ['docs', 'reply']
        for board in results.categories:
            assert board.votes == 0
            assert board.unranked == []
            assert {standing.system for standing in board.standings} == {'a', 'b'}
            assert all(
                standing.votes
                == standing.effective_votes
                == standing.decisive_votes
                == 0
                for standing in board.standings
            )

    def test_drops_the_votes_whose_outputs_left(
        self, families_dir: Path, store: VoteStore
    ) -> None:
        family = small_family(families_dir, store)
        write_outputs('f', [o for o in read_outputs('f') if o.case != 'c2'])

        results = close_round(family, store, AT).results

        assert results.scorer == 'votes'
        assert (results.votes, results.dropped_votes) == (3, 2)
        assert [board.category for board in results.categories] == ['reply']

    def test_serves_and_closes_after_a_case_moves_to_a_new_id(
        self, families_dir: Path, clock: Clock, store: VoteStore
    ) -> None:
        arena = make_arena(families_dir, clock, store)
        view = arena.start('f')
        while view.pair is not None:
            view = arena.vote(view.pair.token, 'a', Defects(), None)
        cases = families_dir / 'f' / 'cases.jsonl'
        cases.write_text(cases.read_text().replace('"c1"', '"c4"'))
        restarted = Arena(
            store, load_families(), RENDER_ORIGIN, clock, np.random.default_rng(0)
        )

        view = restarted.start('f')
        shown = []
        while view.pair is not None:
            shown.append(view.pair.instruction)
            view = restarted.vote(view.pair.token, 'a', Defects(), None)
        results = close_round(load_family('f'), store, AT).results

        assert sorted(shown) == ['Write about c2.', 'Write about c3.']
        assert results.scorer == 'votes'
        assert (results.votes, results.dropped_votes) == (4, 1)

    def test_ranks_only_the_systems_connected_to_the_root(
        self, families_dir: Path, store: VoteStore
    ) -> None:
        cases = {'c1': 'reply', 'c2': 'reply'}
        write_votes_family(families_dir, 'f', ['a', 'b', 'c', 'd'], cases)
        outputs = write_passing_outputs(cases, ['a', 'b', 'c', 'd'])
        session = store.start_session('f', AT)
        for left, right, case in (('a', 'b', 'c1'), ('c', 'd', 'c2')):
            for choice in ('a', 'b', 'a'):
                a, b = outputs[(case, left)], outputs[(case, right)]
                store.record(make_ballot(a, b, session=session.id, choice=choice), AT)

        results = close_round(load_family('f'), store, AT).results

        assert results.scorer == 'votes'
        assert {standing.system for standing in results.standings} == {'a', 'b'}
        assert results.unranked == ['c', 'd']
        (board,) = results.categories
        assert {standing.system for standing in board.standings} == {'a', 'b'}
        assert board.unranked == ['c', 'd']

    def test_ranks_a_system_without_votes_through_the_results_tests_settle(
        self, families_dir: Path, store: VoteStore
    ) -> None:
        tasks = ['t1', 't2', 't3', 't4']
        write_task_family(
            families_dir, *tasks, systems=('a', 'b', 'c'), defects=('testing', 'scope')
        )
        failed = {'gate_failures': [Gate.TESTS], 'reward': 0.0}
        solved_by_c = {'t3', 't4'}
        outputs = {
            slot: output.model_copy(
                update={'reward': 1.0}
                | (failed if (slot[1] == 'c') != (slot[0] in solved_by_c) else {})
            )
            for slot, output in passing_outputs(tasks, ['a', 'b', 'c']).items()
        }
        outputs['t1', 'c'] = outputs['t1', 'c'].model_copy(
            update={'error': 'AgentTimeoutError'}
        )
        write_outputs('f', list(outputs.values()))
        session = store.start_session('f', AT)
        tagged: list[tuple[str, Choice, frozenset[Defect]]] = [
            ('t1', 'a', frozenset({'scope'})),
            ('t2', 'b', frozenset({'scope', 'testing'})),
        ]
        for task, choice, defects in tagged:
            ballot = make_ballot(
                outputs[task, 'a'],
                outputs[task, 'b'],
                session=session.id,
                choice=choice,
                defects=Defects(a=defects),
            )
            store.record(ballot, AT)

        results = close_round(load_family('f'), store, AT).results

        assert results.scorer == 'votes'
        standings = {standing.system: standing for standing in results.standings}
        assert standings.keys() == {'a', 'b', 'c'}
        assert results.unranked == []
        c = standings['c']
        assert (c.votes, c.outputs, c.gate_failure_rate) == (0, 3, 0.5)
        assert list(standings['a'].defects.items()) == [('testing', 1), ('scope', 2)]

    def test_leaves_an_unlisted_system_without_votes_off_the_board(
        self, families_dir: Path, store: VoteStore
    ) -> None:
        cases = {'r0': 'reply', 'r1': 'reply'}
        write_votes_family(families_dir, 'f', ['a', 'b'], cases)
        outputs = write_passing_outputs(cases, ['a', 'b', 'gone'])
        session = store.start_session('f', AT)
        for case in cases:
            ballot = make_ballot(
                outputs[case, 'a'], outputs[case, 'b'], session=session.id
            )
            store.record(ballot, AT)

        results = close_round(load_family('f'), store, AT).results

        assert results.scorer == 'votes'
        assert {standing.system for standing in results.standings} == {'a', 'b'}
        assert results.unranked == []
        assert [board.unranked for board in results.categories] == [[]]

    def test_keeps_sparse_connected_points_on_overall_and_category_boards(
        self, families_dir: Path, store: VoteStore
    ) -> None:
        cases = {f'r{i}': 'reply' for i in range(5)} | {'d0': 'docs', 'd1': 'docs'}
        write_votes_family(families_dir, 'f', ['a', 'b', 'c', 'd'], cases)
        outputs = write_passing_outputs(cases, ['a', 'b', 'c', 'd'])
        session = store.start_session('f', AT)
        pairs = [(f'r{i}', opponent) for i in range(5) for opponent in 'bc']
        for case, opponent in [*pairs, ('r0', 'd'), ('d0', 'b'), ('d1', 'c')]:
            a, b = outputs[case, 'a'], outputs[case, opponent]
            store.record(make_ballot(a, b, session=session.id), AT)

        results = close_round(load_family('f'), store, AT).results

        assert results.scorer == 'votes'
        assert {standing.system for standing in results.standings} == {
            'a',
            'b',
            'c',
            'd',
        }
        assert results.unranked == []
        docs = next(board for board in results.categories if board.category == 'docs')
        assert {standing.system for standing in docs.standings} == {'a', 'b', 'c'}
        assert docs.unranked == ['d']

    def test_records_raw_decisive_coverage_and_its_frozen_preliminary_rule(
        self, families_dir: Path, store: VoteStore
    ) -> None:
        cases = {f'c{i:02}': 'reply' for i in range(45)}
        write_votes_family(families_dir, 'f', ['x', 'y', 'z'], cases)
        outputs = write_passing_outputs(cases, ['x', 'y', 'z'])
        session = store.start_session('f', AT)
        for i, case in enumerate(cases):
            x, y, z = (outputs[(case, system)] for system in 'xyz')
            choice: Choice = 'a' if i % 5 < 3 else 'b'
            store.record(make_ballot(x, y, session=session.id, choice=choice), AT)
            if i < 10:
                store.record(make_ballot(z, x, session=session.id, choice='tie'), AT)

        results = close_round(load_family('f'), store, AT).results

        assert results.scorer == 'votes'
        expected = {'x': 45, 'y': 45, 'z': 0}
        assert results.rules.preliminary_decisive_votes == 40
        assert results.rules.preliminary_decisive_vote_unit == 'raw-votes'
        assert {s.system: s.decisive_votes for s in results.standings} == expected
        (board,) = results.categories
        assert {s.system: s.decisive_votes for s in board.standings} == expected

    def test_measures_a_preference_for_longer_outputs(
        self, families_dir: Path, store: VoteStore
    ) -> None:
        cases = {f'c{i}': 'reply' for i in range(8)}
        write_votes_family(families_dir, 'f', ['x', 'y'], cases)
        words: dict[str, Callable[[int], int]] = {
            'x': lambda i: 5 + 3 * i,
            'y': lambda i: 26 - 3 * i,
        }
        passing = passing_outputs(cases, ['x', 'y'])
        outputs = {
            (f'c{i}', system): passing[(f'c{i}', system)].model_copy(
                update={'text': ' '.join(['word'] * count(i))}
            )
            for i in range(8)
            for system, count in words.items()
        }
        write_outputs('f', list(outputs.values()))
        session = store.start_session('f', AT)
        for i in range(8):
            x, y = outputs[(f'c{i}', 'x')], outputs[(f'c{i}', 'y')]
            longer: Choice = 'a' if i >= 4 else 'b'
            shorter: Choice = 'b' if longer == 'a' else 'a'
            store.record(make_ballot(x, y, session=session.id, choice=longer), AT)
            store.record(make_ballot(y, x, session=session.id, choice=shorter), AT)
        x, y = outputs[('c0', 'x')], outputs[('c0', 'y')]
        store.record(make_ballot(x, y, session=session.id), AT)

        results = close_round(load_family('f'), store, AT).results

        assert results.scorer == 'votes'
        assert results.length is not None
        assert results.length > 50

    @pytest.mark.parametrize(
        ('texts', 'swap'),
        [
            pytest.param(
                {'x': '{case} answer', 'y': '{case} reply'}, False, id='equal'
            ),
            pytest.param(
                {'x': '{case} gives an answer', 'y': '{case}'},
                False,
                id='constant-ratio',
            ),
            pytest.param(
                {'x': '{case} gives an answer', 'y': '{case}'},
                True,
                id='pane-swapped-system-lengths',
            ),
        ],
    )
    def test_leaves_length_out_when_it_cannot_be_identified(
        self,
        families_dir: Path,
        store: VoteStore,
        texts: dict[str, str],
        swap: bool,
    ) -> None:
        cases = {f'c{i}': 'reply' for i in range(4)}
        write_votes_family(families_dir, 'f', ['x', 'y'], cases)
        outputs = {
            (case, system): output.model_copy(
                update={'text': texts[system].format(case=case)}
            )
            for (case, system), output in passing_outputs(cases, ['x', 'y']).items()
        }
        write_outputs('f', list(outputs.values()))
        session = store.start_session('f', AT)
        for case in cases:
            x, y = outputs[(case, 'x')], outputs[(case, 'y')]
            for choice in ('a', 'a', 'a', 'b'):
                store.record(make_ballot(x, y, session=session.id, choice=choice), AT)
                if swap:
                    reverse: Choice = 'b' if choice == 'a' else 'a'
                    store.record(
                        make_ballot(y, x, session=session.id, choice=reverse), AT
                    )

        results = close_round(load_family('f'), store, AT).results

        assert results.scorer == 'votes'
        assert {standing.system for standing in results.standings} == {'x', 'y'}
        assert results.length is None
        if swap:
            scores = {standing.system: standing.score for standing in results.standings}
            assert scores['x'] > scores['y']

    @pytest.mark.parametrize('empty', [False, True], ids=['voted', 'empty-session'])
    def test_agrees_across_halves_split_by_session_parity(
        self, families_dir: Path, store: VoteStore, empty: bool
    ) -> None:
        write_votes_family(families_dir, 'f', ['a', 'b', 'c'], {'c1': 'reply'})
        outputs = write_passing_outputs({'c1': 'reply'}, ['a', 'b', 'c'])
        strong = [('a', 'b'), ('b', 'c'), ('a', 'c')] * 3
        weak = [('c', 'b'), ('b', 'a'), ('c', 'a')]
        for day, pairs in enumerate((strong, strong, weak, weak)):
            session = store.start_session('f', AT + timedelta(days=day))
            for i, (winner, loser) in enumerate(pairs):
                a, b = outputs[('c1', winner)], outputs[('c1', loser)]
                ballot = (
                    make_ballot(b, a, session=session.id, choice='b')
                    if i % 2
                    else make_ballot(a, b, session=session.id)
                )
                store.record(ballot, AT)
            if empty and day == 0:
                store.start_session('f', AT + timedelta(hours=1))

        results = close_round(load_family('f'), store, AT).results

        assert results.scorer == 'votes'
        assert results.sessions == 4
        assert results.split_half_tau == pytest.approx(1.0)

    def test_leaves_split_half_agreement_out_when_a_half_misses_the_root(
        self, families_dir: Path, store: VoteStore
    ) -> None:
        write_votes_family(families_dir, 'f', ['a', 'b', 'c'], {'c1': 'reply'})
        outputs = write_passing_outputs({'c1': 'reply'}, ['a', 'b', 'c'])
        halves = ([('a', 'b'), ('a', 'c')] * 4, [('b', 'c')] * 3)
        for day, pairs in enumerate(halves):
            session = store.start_session('f', AT + timedelta(days=day))
            for winner, loser in pairs:
                a, b = outputs['c1', winner], outputs['c1', loser]
                store.record(make_ballot(a, b, session=session.id), AT)

        results = close_round(load_family('f'), store, AT).results

        assert results.scorer == 'votes'
        assert results.anchor == 'a'
        assert results.split_half_tau is None

    def test_measures_rater_agreement_on_swapped_repeats(
        self, families_dir: Path, store: VoteStore
    ) -> None:
        family = small_family(families_dir, store)
        first, _, third, *_ = store.votes('f')
        session = store.start_session('f', AT)
        repeats: list[tuple[Vote, Choice]] = [(first, 'b'), (third, 'a')]
        for original, choice in repeats:
            repeat = Ballot(
                session=session.id,
                case=original.case,
                a=original.b,
                b=original.a,
                choice=choice,
                duration_sec=10.0,
                repeat_of=original.id,
            )
            store.record(repeat, AT)

        results = close_round(family, store, AT).results

        assert results.scorer == 'votes'
        agreement = results.rater_agreement
        assert (agreement.repeats, agreement.agreement) == (2, 0.5)
        assert agreement.below_floor

    def test_anchors_each_category_at_the_board_anchor(
        self, families_dir: Path, store: VoteStore
    ) -> None:
        family = small_family(families_dir, store)

        results = close_round(family, store, AT).results

        assert results.scorer == 'votes'
        assert (results.reference, results.anchor) == ('large', 'large')
        assert [board.category for board in results.categories] == ['docs', 'reply']
        for board in results.categories:
            anchor = next(s for s in board.standings if s.system == results.anchor)
            assert anchor.score == pytest.approx(1000)

    def test_anchors_the_most_voted_system_without_the_reference(
        self, families_dir: Path, store: VoteStore
    ) -> None:
        cases = {f'c{i}': 'reply' for i in range(8)}
        systems = ['large', 'small', 'medium', 'tiny']
        write_votes_family(families_dir, 'f', systems, cases)
        outputs = write_passing_outputs(cases, systems)
        session = store.start_session('f', AT)
        for case in cases:
            for opponent, choice in (('small', 'a'), ('tiny', 'b')):
                a, b = outputs[(case, 'medium')], outputs[(case, opponent)]
                store.record(make_ballot(a, b, session=session.id, choice=choice), AT)

        results = close_round(load_family('f'), store, AT).results

        assert results.scorer == 'votes'
        assert (results.reference, results.anchor) == ('large', 'medium')
        assert {standing.system for standing in results.standings} == {
            'medium',
            'small',
            'tiny',
        }
        assert results.unranked == ['large']

    def test_keeps_a_sparse_reference_as_the_anchor_when_it_is_connected(
        self, families_dir: Path, store: VoteStore
    ) -> None:
        cases = {'r0': 'reply'} | {f'c{i}': 'reply' for i in range(8)}
        systems = ['ref', 'a', 'b']
        write_votes_family(families_dir, 'f', systems, cases)
        outputs = write_passing_outputs(cases, systems)
        session = store.start_session('f', AT)
        for case, left, right in [('r0', 'ref', 'a')] + [
            (f'c{i}', 'a', 'b') for i in range(8)
        ]:
            a, b = outputs[(case, left)], outputs[(case, right)]
            store.record(make_ballot(a, b, session=session.id), AT)

        results = close_round(load_family('f'), store, AT).results

        assert results.scorer == 'votes'
        assert (results.reference, results.anchor) == ('ref', 'ref')
        assert {standing.system for standing in results.standings} == {'ref', 'a', 'b'}
        assert results.unranked == []

    def test_roots_each_category_inside_the_anchors_component(
        self, families_dir: Path, store: VoteStore
    ) -> None:
        cases = {f'r{i}': 'reply' for i in range(5)} | {
            f'd{i}': 'docs' for i in range(3)
        }
        systems = ['a', 'b', 'c', 'd']
        write_votes_family(families_dir, 'f', systems, cases)
        outputs = write_passing_outputs(cases, systems)
        session = store.start_session('f', AT)
        votes = (
            [(f'r{i}', 'a', 'b') for i in range(5)]
            + [(f'd{i}', 'a', 'b') for i in range(2)]
            + [(f'd{i}', 'c', 'd') for i in range(3)]
        )
        for case, left, right in votes:
            a, b = outputs[(case, left)], outputs[(case, right)]
            store.record(make_ballot(a, b, session=session.id), AT)

        results = close_round(load_family('f'), store, AT).results

        assert results.scorer == 'votes'
        assert results.anchor == 'a'
        docs = next(board for board in results.categories if board.category == 'docs')
        assert {standing.system for standing in docs.standings} == {'a', 'b'}
        assert docs.unranked == ['c', 'd']

    def test_breaks_a_category_root_vote_tie_by_the_systems_order(
        self, families_dir: Path, store: VoteStore
    ) -> None:
        cases = {'r0': 'reply'} | {f'd{i}': 'docs' for i in range(7)}
        systems = ['x', 'b', 'c', 'd']
        write_votes_family(families_dir, 'f', systems, cases)
        outputs = write_passing_outputs(cases, systems)
        session = store.start_session('f', AT)
        choices: list[Choice] = ['a', 'b', 'a', 'b', 'tie']
        votes: list[tuple[str, str, str, Choice]] = [
            ('r0', 'x', 'b', 'a'),
            *((f'd{i}', 'x', 'b', choice) for i, choice in enumerate(choices)),
            ('d5', 'x', 'c', 'a'),
            *(('d6', 'c', 'd', 'a') for _ in range(5)),
        ]
        for case, left, right, choice in votes:
            a, b = outputs[(case, left)], outputs[(case, right)]
            store.record(make_ballot(a, b, session=session.id, choice=choice), AT)

        results = close_round(load_family('f'), store, AT).results

        assert results.scorer == 'votes'
        assert results.anchor == 'x'
        docs = next(board for board in results.categories if board.category == 'docs')
        assert {standing.system for standing in docs.standings} == {'x', 'b', 'c', 'd'}
        assert docs.unranked == []

    def test_keeps_a_category_connected_to_a_sparsely_voted_anchor(
        self, families_dir: Path, store: VoteStore
    ) -> None:
        cases = {f'r{i}': 'reply' for i in range(5)} | {
            f'd{i}': 'docs' for i in range(3)
        }
        systems = ['a', 'b', 'c']
        write_votes_family(families_dir, 'f', systems, cases)
        outputs = write_passing_outputs(cases, systems)
        session = store.start_session('f', AT)
        votes = (
            [
                (f'r{i}', 'a', opponent, choice)
                for i in range(5)
                for opponent, choice in (('b', 'a'), ('c', 'b'))
            ]
            + [('d0', 'a', 'b', 'a')]
            + [(f'd{i}', 'b', 'c', 'a') for i in range(3) for _ in range(3)]
        )
        for case, left, right, choice in votes:
            a, b = outputs[(case, left)], outputs[(case, right)]
            store.record(make_ballot(a, b, session=session.id, choice=choice), AT)

        results = close_round(load_family('f'), store, AT).results

        assert results.scorer == 'votes'
        assert results.anchor == 'a'
        assert [board.category for board in results.categories] == ['docs', 'reply']
        for board in results.categories:
            assert {s.system for s in board.standings} == {'a', 'b', 'c'}
            assert next(s.score for s in board.standings if s.system == 'a') == 1000

    def test_needs_votes(self, families_dir: Path, store: VoteStore) -> None:
        cases = {'c1': 'reply'}
        write_votes_family(families_dir, 'f', ['a', 'b'], cases)
        write_passing_outputs(cases, ['a', 'b'])

        with pytest.raises(ValueError, match='Family `f` has no votes yet'):
            close_round(load_family('f'), store, AT)

    def test_needs_outputs_on_the_current_cases(
        self, families_dir: Path, store: VoteStore
    ) -> None:
        family = small_family(families_dir, store)
        retired = read_outputs('f')[0].model_copy(update={'case': 'gone'})
        write_outputs('f', [retired])

        with pytest.raises(ValueError) as raised:
            close_round(family, store, AT)
        assert str(raised.value) == (
            'Family `f` has no outputs on its current cases. '
            'Run `uv run bench run f` first.'
        )

    def test_needs_votes_on_the_current_outputs(
        self, families_dir: Path, store: VoteStore
    ) -> None:
        family = small_family(families_dir, store)
        write_outputs(
            'f',
            [
                output.model_copy(
                    update={'job': 'job-new', 'trial': f'new-{output.trial}'}
                )
                for output in read_outputs('f')
            ],
        )

        with pytest.raises(
            ValueError, match='Family `f` has votes, but none compares two of its'
        ):
            close_round(family, store, AT)

    def test_breaks_a_root_vote_tie_by_the_systems_order(
        self, families_dir: Path, store: VoteStore
    ) -> None:
        cases = {f'c{i}': 'reply' for i in range(8)}
        systems = ['ref', 'zeta', 'alpha']
        write_votes_family(families_dir, 'f', systems, cases)
        outputs = write_passing_outputs(cases, systems)
        session = store.start_session('f', AT)
        for case in cases:
            a, b = outputs[(case, 'zeta')], outputs[(case, 'alpha')]
            store.record(make_ballot(a, b, session=session.id), AT)

        results = close_round(load_family('f'), store, AT).results

        assert results.scorer == 'votes'
        assert (results.reference, results.anchor) == ('ref', 'zeta')
        assert {standing.system for standing in results.standings} == {
            'alpha',
            'zeta',
        }

    def test_anchors_a_fallback_when_the_reference_loses_its_outputs(
        self, families_dir: Path, store: VoteStore
    ) -> None:
        family = small_family(families_dir, store)
        write_outputs('f', [o for o in read_outputs('f') if o.system != 'large'])

        results = close_round(family, store, AT).results

        assert results.scorer == 'votes'
        assert (results.reference, results.anchor) == ('large', 'medium')
        assert {standing.system for standing in results.standings} == {
            'medium',
            'small',
        }
        assert results.unranked == []
        assert [board.category for board in results.categories] == ['misc', 'reply']

    def test_ignores_a_retired_output_under_a_stale_fingerprint(
        self, families_dir: Path, store: VoteStore
    ) -> None:
        family = small_family(families_dir, store)
        retired = read_outputs('f')[0].model_copy(
            update={'trial': 'old', 'case': 'gone', 'fingerprint': 'fp-old'}
        )
        write_outputs('f', [*read_outputs('f'), retired])

        results = close_round(family, store, AT).results

        assert results.scorer == 'votes'
        assert (results.votes, results.dropped_votes) == (5, 0)

    def test_rejects_outputs_under_several_fingerprints(
        self, families_dir: Path, store: VoteStore
    ) -> None:
        family = small_family(families_dir, store)
        outputs = read_outputs('f')
        old = outputs[0].model_copy(update={'trial': 'old', 'fingerprint': 'fp-old'})
        write_outputs('f', [*outputs, old])

        with pytest.raises(
            ValueError, match='System `large` has outputs under several'
        ):
            close_round(family, store, AT)

    def test_rejects_an_unranked_system_under_several_fingerprints(
        self, families_dir: Path, store: VoteStore
    ) -> None:
        cases = {'c1': 'reply', 'c2': 'reply'}
        write_votes_family(families_dir, 'f', ['a', 'b', 'c', 'd'], cases)
        outputs = passing_outputs(cases, ['a', 'b', 'c', 'd'])
        old = outputs[('c2', 'd')].model_copy(
            update={'trial': 'old', 'fingerprint': 'fp-old', 'text': 'd before'}
        )
        write_outputs('f', [*outputs.values(), old])
        session = store.start_session('f', AT)
        for left, right, case in (('a', 'b', 'c1'), ('c', 'd', 'c2')):
            a, b = outputs[(case, left)], outputs[(case, right)]
            store.record(make_ballot(a, b, session=session.id), AT)

        with pytest.raises(ValueError, match='System `d` has outputs under several'):
            close_round(load_family('f'), store, AT)

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
        self, store: VoteStore, trials: list[dict[str, str]], message: str
    ) -> None:
        write_outputs(
            'smoke',
            [
                make_output(**{'case': 'echo', 'system': 'large'} | trial)
                for trial in trials
            ],
        )

        with pytest.raises(ValueError, match=message):
            close_round(load_family('smoke'), store, AT)

    def test_reports_pass_rates_for_a_tests_family(self, store: VoteStore) -> None:
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

        closed = close_round(load_family('smoke'), store, AT)
        results = closed.results

        assert results.scorer == 'tests'
        (standing,) = results.standings
        assert (standing.trials, standing.errored) == (3, 1)
        assert standing.pass_rate == pytest.approx(1 / 3)
        assert standing.mean_cost_usd == pytest.approx(0.01)
        assert standing.p50_latency_sec == 3.0
        assert closed.systems['large'].display_name == 'Public large'
        assert set(results.model_dump()) == {'scorer', 'standings'}
        assert 'fingerprint' not in standing.model_dump()


class TestWriteReport:
    def test_writes_reports_family_round_json(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(report, 'REPORTS_DIR', tmp_path / 'reports')
        closed = Report.model_validate_json(REPORT_FIXTURE.read_text())

        path = write_report(closed)

        assert path == tmp_path / 'reports' / 'arena' / '2026-10-21.json'
        assert Report.model_validate_json(path.read_text()) == closed


class TestReadReports:
    def test_reads_strict_prior_rounds_in_closing_order(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(report, 'REPORTS_DIR', tmp_path / 'reports')
        closed = Report.model_validate_json(REPORT_FIXTURE.read_text())
        earlier = closed.model_copy(update={'round': 'z-early', 'closed_at': AT})
        middle = closed.model_copy(
            update={'round': 'a-middle', 'closed_at': AT + timedelta(hours=12)}
        )
        boundary = AT + timedelta(days=1)
        same_time = closed.model_copy(
            update={'round': 'boundary', 'closed_at': boundary}
        )
        later = closed.model_copy(
            update={'round': 'future', 'closed_at': AT + timedelta(days=2)}
        )
        for item in (later, middle, same_time, earlier):
            write_report(item)

        reports = report.read_reports(closed.family, boundary)

        assert reports == [earlier, middle]
        assert report.read_reports('missing', boundary) == []

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
            report.read_reports(closed.family, closed.closed_at + timedelta(days=1))


class TestTextLength:
    @pytest.mark.parametrize(
        ('text', 'length'),
        [
            ('Two words.', 2),
            ('用一句话欢迎。', 7),
            ('运行 nix flake check 即可', 7),
            ('增长一〇〇%', 6),
            ('', 0),
        ],
        ids=['words', 'han', 'mixed', 'ling', 'empty'],
    )
    def test_counts_han_characters_and_other_words(
        self, text: str, length: int
    ) -> None:
        assert text_length(text) == length
