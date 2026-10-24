import json
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

import jsonschema
import numpy as np
import pytest
from pydantic import ValidationError

from assistant_bench import report
from assistant_bench.arena.snapshots import HumanEvidence, OutputMeasurement
from assistant_bench.families import load_family
from assistant_bench.outputs import write_outputs
from assistant_bench.report import (
    Report,
    write_report,
)
from assistant_bench.scoring import ranking
from assistant_bench.scoring.weighting import actor_weights
from assistant_bench_arena.models import Choice
from snapshot_support import make_analysis, make_capped_analysis, make_evidence
from support import (
    AT,
    EARLY_REPORT_FIXTURE,
    REPORT_FIXTURE,
    REPORT_SCHEMA_FIXTURE,
    make_output,
)


class TestReport:
    @pytest.mark.parametrize(
        'fixture', [REPORT_FIXTURE, EARLY_REPORT_FIXTURE], ids=['full', 'early']
    )
    def test_roundtrips_unrecorded_unit_reports_without_adding_provenance(
        self, fixture: Path
    ) -> None:
        recorded = fixture.read_text()

        parsed = Report.model_validate_json(recorded)

        assert parsed.results.scorer == 'votes'
        assert parsed.results.provenance.status == 'unrecorded'
        assert f'{parsed.model_dump_json(indent=2)}\n' == recorded

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
    def test_normalizes_an_offset_closing_time_before_naming_the_round(self) -> None:
        write_outputs('smoke', [make_output(case='echo', system='large')])
        utc = AT.replace(hour=23)
        local = utc.astimezone(timezone(timedelta(hours=9)))

        closed = report.close(
            load_family('smoke'),
            local,
            system_names={'large': 'Public large'},
            previous_reports=[],
        )

        assert closed.round == '2026-10-21'
        assert closed.closed_at == utc
        assert closed.closed_at.tzinfo == UTC

    def test_rejects_a_naive_public_closing_time(self) -> None:
        with pytest.raises(ValueError):
            report.close(
                load_family('smoke'),
                AT.replace(tzinfo=None),
                system_names={},
                previous_reports=[],
            )

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
                system_names={'large': 'Public large'},
                previous_reports=[],
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
            system_names={'large': 'Public large'},
            previous_reports=[],
        )
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


class TestVotesResults:
    def test_counts_defects_on_the_output_side_and_ignores_errored_measurements(
        self,
    ) -> None:
        rows = (
            replace(
                make_evidence(0, choice='both_bad'),
                defects_a=('facts',),
                defects_b=('tone',),
            ),
            replace(make_evidence(1, systems=('b', 'a')), defects_b=('facts',)),
        )
        analysis = make_analysis(rows)
        outputs = tuple(
            OutputMeasurement(
                case=f'case-{index}',
                system=system,
                errored=index == 1 and system == 'a',
                verdict='fail' if index == 1 else 'pass',
                cost_usd=100.0 if index == 1 else 0.01,
                latency_sec=100.0 if index == 1 else 2.0,
                output_tokens=100 if index == 1 else 20,
            )
            for index in range(2)
            for system in ('a', 'b')
        )

        results = report.votes_results(replace(analysis, outputs=outputs))

        standings = {row.system: row for row in results.standings}
        assert standings['a'].defects == {'facts': 2}
        assert standings['b'].defects == {'facts': 0}
        assert standings['a'].both_bad_rate == 0.5
        assert standings['a'].outputs == 1
        assert standings['a'].gate_failure_rate == 0.5
        assert standings['a'].mean_cost_usd == 0.01
        assert standings['a'].p50_latency_sec == 2.0
        assert standings['a'].output_tokens is not None
        assert standings['a'].output_tokens.p50 == 20.0
        assert standings['b'].mean_cost_usd == pytest.approx(50.005)

    @pytest.mark.parametrize('choice', ['a', 'b', 'tie', 'both_bad'])
    def test_compares_swapped_repeats_without_adding_them_to_the_fit(
        self, choice: Choice
    ) -> None:
        original = make_evidence(0, choice='a')
        repeat = replace(
            make_evidence(1, choice=choice, systems=('b', 'a')),
            repeat_of=original.receipt,
        )

        results = report.votes_results(make_analysis((original,), repeats=(repeat,)))

        assert (results.votes, results.effective_votes, results.dropped_votes) == (
            1,
            1,
            1,
        )
        assert results.rater_agreement.repeats == 1
        assert results.rater_agreement.agreement == float(choice == 'b')
        assert results.rater_agreement.below_floor == (choice != 'b')

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
        assert results.rater_agreement.agreement is None
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

    def test_reads_a_valid_serialized_capped_report(self) -> None:
        results = report.votes_results(make_capped_analysis())

        parsed = report.VotesResults.model_validate_json(results.model_dump_json())

        assert parsed == results

    @pytest.mark.parametrize(
        'change',
        [
            'overall-mass',
            'head-total',
            'mirror-mass',
            'mirror-outcomes',
            'mirror-rate',
            'category-row',
            'category-total',
            'duplicate-category',
            'standing-mass',
        ],
    )
    def test_rejects_aggregates_that_cannot_share_one_frozen_weight_sequence(
        self, change: str
    ) -> None:
        payload = report.votes_results(make_capped_analysis()).model_dump(mode='json')
        first = payload['head_to_head'][0]
        mirror = next(
            row
            for row in payload['head_to_head']
            if (row['system'], row['opponent']) == (first['opponent'], first['system'])
        )
        match change:
            case 'overall-mass':
                payload['effective_votes'] = 1
            case 'head-total':
                payload['votes'] += 1
            case 'mirror-mass':
                first['effective_votes'] += 0.25
                mirror['effective_votes'] -= 0.25
            case 'mirror-outcomes':
                first['wins'] += 1
                first['losses'] -= 1
            case 'mirror-rate':
                first['win_rate'] = 0.1
            case 'category-row':
                payload['categories'][0]['effective_votes'] = 1
            case 'category-total':
                payload['categories'][1]['effective_votes'] = (
                    payload['effective_votes']
                    - payload['categories'][0]['effective_votes']
                    + 1
                )
            case 'duplicate-category':
                payload['categories'][1]['category'] = payload['categories'][0][
                    'category'
                ]
            case 'standing-mass':
                payload['standings'][0]['effective_votes'] = (
                    payload['effective_votes'] + 1
                )
            case _:
                raise AssertionError(change)

        with pytest.raises(ValidationError):
            report.VotesResults.model_validate_json(json.dumps(payload))

    def test_rejects_standing_totals_above_both_sides_of_the_full_evaluation(
        self,
    ) -> None:
        base = make_capped_analysis()
        bridge = ranking.Comparison('b', 'x', 0.5, 'bridge', 0.25, sided=False)
        connected = make_analysis(
            base.ordinary,
            settlements=(bridge,),
            repeats=base.repeats,
            categories=(*base.categories, ('bridge', 'other')),
        )
        payload = report.votes_results(connected).model_dump(mode='json')
        standing = next(row for row in payload['standings'] if row['system'] == 'x')
        standing['effective_votes'] += 1

        with pytest.raises(ValidationError, match='Standing totals'):
            report.VotesResults.model_validate_json(json.dumps(payload))

    @pytest.mark.parametrize(
        ('field', 'value'),
        [
            ('name', 'unknown-method'),
            ('version', 2),
            ('cap', 0),
            ('cap', True),
            ('scope', 'category'),
            ('repeat_handling', 'included'),
        ],
        ids=['name', 'version', 'nonpositive-cap', 'boolean-cap', 'scope', 'repeats'],
    )
    def test_rejects_unsupported_or_inconsistent_human_weight_methods(
        self, field: str, value: str | int
    ) -> None:
        payload = report.votes_results(make_capped_analysis()).model_dump(mode='json')
        payload['method']['human_weights'][field] = value

        with pytest.raises(ValidationError):
            report.VotesResults.model_validate_json(json.dumps(payload))

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
            for value in (-1.0, float('inf'), float('nan'), 1000.0)
        ],
        ids=lambda value: str(value),
    )
    def test_rejects_invalid_effective_mass_at_every_aggregate(
        self, scope: str, value: float
    ) -> None:
        payload = report.votes_results(make_capped_analysis()).model_dump(mode='json')
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
    def test_requires_private_free_ledger_provenance_for_capped_reports(
        self, change: str
    ) -> None:
        payload = report.votes_results(make_capped_analysis()).model_dump(mode='json')
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
        self,
    ) -> None:
        analysis = make_capped_analysis()

        results = report.votes_results(analysis)

        assert results.votes == 275
        assert results.effective_votes == pytest.approx(125)
        assert results.dropped_votes == 1
        assert results.sessions == 4
        assert results.unranked == ['x', 'y']
        assert results.rater_agreement.model_dump() == {
            'repeats': 1,
            'agreement': 1.0,
            'below_floor': False,
        }

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
        assert set(categories) == {'main', 'other'}
        assert categories['main'].votes == 15
        assert categories['main'].effective_votes == pytest.approx(9)
        assert all(
            row.effective_votes == pytest.approx(9)
            for row in categories['main'].standings
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

        weights = actor_weights(actors, 100)
        ordinary = tuple(
            replace(row, comparison=row.comparison._replace(weight=weight))
            for row, weight in zip(rows, weights, strict=True)
        )
        analysis = make_analysis(ordinary)

        results = report.votes_results(analysis)

        assert results.split_half_tau == pytest.approx(-1.0)


class TestWriteReport:
    @pytest.mark.parametrize(
        ('field', 'value'),
        [
            ('family', '../outside'),
            ('family', '/outside'),
            ('family', 'family\n'),
            ('round', '../outside'),
            ('round', '2026-10-21\n'),
            ('round', '２０２６-１０-２１'),
        ],
    )
    def test_refuses_unsafe_paths_even_when_a_caller_bypasses_model_validation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, value: str
    ) -> None:
        directory = tmp_path / 'reports'
        monkeypatch.setattr(report, 'REPORTS_DIR', directory)
        original = Report.model_validate_json(REPORT_FIXTURE.read_text())
        invalid = original.model_copy(update={field: value})

        with pytest.raises(ValidationError):
            write_report(invalid)

        assert not directory.exists()

    def test_writes_reports_family_round_json(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(report, 'REPORTS_DIR', tmp_path / 'reports')
        closed = Report.model_validate_json(REPORT_FIXTURE.read_text())

        path = write_report(closed)

        assert path == tmp_path / 'reports' / 'arena' / '2026-10-21.json'
        assert Report.model_validate_json(path.read_text()) == closed


class TestReadReports:
    @pytest.mark.parametrize(
        'family', ['../outside', '/outside', 'family\n', 'Uppercase']
    )
    def test_rejects_an_invalid_family_before_reading_any_report(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, family: str
    ) -> None:
        monkeypatch.setattr(report, 'REPORTS_DIR', tmp_path / 'missing')

        with pytest.raises(ValidationError):
            report.read_reports(family, AT)

    def test_reads_strict_prior_rounds_in_closing_order(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(report, 'REPORTS_DIR', tmp_path / 'reports')
        closed = Report.model_validate_json(REPORT_FIXTURE.read_text())
        earlier = closed.model_copy(update={'round': '2026-10-01', 'closed_at': AT})
        middle = closed.model_copy(
            update={'round': '2026-10-02', 'closed_at': AT + timedelta(hours=12)}
        )
        boundary = AT + timedelta(days=1)
        same_time = closed.model_copy(
            update={'round': '2026-10-03', 'closed_at': boundary}
        )
        later = closed.model_copy(
            update={'round': '2026-10-04', 'closed_at': AT + timedelta(days=2)}
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
