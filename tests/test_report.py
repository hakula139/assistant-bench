import json
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, NamedTuple

import jsonschema
import numpy as np
import pytest

from assistant_bench import report
from assistant_bench.arena.service import Arena, load_families
from assistant_bench.arena.store import Ballot, Choice, Defects, Vote, VoteStore
from assistant_bench.families import Family, Gate, load_family
from assistant_bench.outputs import Output, read_outputs, write_outputs
from assistant_bench.report import (
    Decision,
    Distribution,
    Interval,
    Report,
    Standing,
    close,
    decide,
    text_length,
    write_report,
)
from assistant_bench.scoring.ranking import Ratings
from support import (
    AT,
    EARLY_REPORT_FIXTURE,
    RENDER_ORIGIN,
    REPORT_FIXTURE,
    REPORT_SCHEMA_FIXTURE,
    SYNTHETIC_CLOSE,
    SYNTHETIC_EARLY_CLOSE,
    Clock,
    make_arena,
    make_ballot,
    make_output,
    passing_outputs,
    write_synthetic_round,
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


def small_family(families_dir: Path, store: VoteStore) -> Family:
    """Write family `f` with three systems and a few hand-placed votes.

    Tied votes on five more cases keep every system connected to `large` in
    nearly every bootstrap resample, so all three are ranked.
    """
    ties = [f't{i}' for i in range(5)]
    cases = {'c1': 'reply', 'c2': 'docs', 'c3': 'misc'} | dict.fromkeys(ties, 'reply')
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
    for case in ties:
        votes += [
            (case, 'large', 'medium', 'tie', Defects(), None),
            (case, 'small', 'large', 'tie', Defects(), None),
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

        closed = close(load_family('arena'), store, closed_at)

        written = json.loads(closed.model_dump_json())
        if json.loads(fixture.read_text()) != approx(written):
            stale_fixture(fixture, f'{closed.model_dump_json(indent=2)}\n')

    def test_reports_aggregates_under_public_names_only(
        self, families_dir: Path, store: VoteStore
    ) -> None:
        family = small_family(families_dir, store)

        closed = close(family, store, AT).model_dump_json()

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

        results = close(family, store, AT).results

        assert results.scorer == 'votes'
        defects = {standing.system: standing.defects for standing in results.standings}
        assert defects['large']['length'] == 1
        assert defects['medium']['length'] == 0
        assert defects['small']['tone'] == 1
        assert defects['medium']['tone'] == 0
        both_bad = {s.system: s.both_bad_rate for s in results.standings}
        assert both_bad == {'large': 1 / 13, 'medium': 0.0, 'small': 1 / 9}
        large_small = next(
            row
            for row in results.head_to_head
            if (row.system, row.opponent) == ('large', 'small')
        )
        assert (large_small.wins, large_small.losses, large_small.both_bad) == (0, 1, 1)
        assert large_small.win_rate == pytest.approx(3 / 7)
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

        results = close(family, store, AT).results

        assert results.scorer == 'votes'
        large = next(s for s in results.standings if s.system == 'large')
        assert large.outputs == 8
        assert large.gate_failure_rate == 0.0
        assert large.mean_cost_usd == pytest.approx(0.02)
        assert large.p50_latency_sec == 5.0
        assert large.latency_sec == Distribution(p10=5.0, p50=5.0, p90=5.0)
        assert large.output_tokens == Distribution(p10=10.0, p50=10.0, p90=10.0)

    def test_lets_the_results_that_gate_failures_settle_replace_votes(
        self, families_dir: Path, store: VoteStore
    ) -> None:
        family = small_family(families_dir, store)
        before = close(family, store, AT).results
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

        after = close(family, store, AT).results

        assert before.scorer == after.scorer == 'votes'
        small_before, small_after = (
            next(s for s in results.standings if s.system == 'small')
            for results in (before, after)
        )
        assert small_after.score < small_before.score - 100
        assert (small_before.votes, small_after.votes) == (9, 0)
        assert small_after.both_bad_rate is None
        assert small_after.gate_failure_rate == 1.0
        assert (after.votes, after.dropped_votes) == (6, 9)
        assert all('small' not in (r.system, r.opponent) for r in after.head_to_head)

    def test_drops_the_votes_whose_outputs_left(
        self, families_dir: Path, store: VoteStore
    ) -> None:
        family = small_family(families_dir, store)
        write_outputs('f', [o for o in read_outputs('f') if o.case != 'c2'])

        results = close(family, store, AT).results

        assert results.scorer == 'votes'
        assert (results.votes, results.dropped_votes) == (13, 2)
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
        results = close(load_family('f'), store, AT).results

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

        results = close(load_family('f'), store, AT).results

        assert results.scorer == 'votes'
        assert {standing.system for standing in results.standings} == {'a', 'b'}
        assert results.unranked == ['c', 'd']
        (board,) = results.categories
        assert {standing.system for standing in board.standings} == {'a', 'b'}
        assert board.unranked == ['c', 'd']

    def test_leaves_out_a_system_whose_outputs_only_settle_results(
        self, families_dir: Path, store: VoteStore
    ) -> None:
        cases = {'c1': 'reply', 'c2': 'reply'}
        write_votes_family(families_dir, 'f', ['a', 'b', 'c'], cases)
        outputs = write_passing_outputs(cases, ['a', 'b', 'c'])
        write_outputs(
            'f',
            [
                output.model_copy(update={'gate_failures': [Gate.TRUNCATED]})
                if output.system == 'c'
                else output
                for output in outputs.values()
            ],
        )
        session = store.start_session('f', AT)
        for case in cases:
            for choice in ('a', 'b', 'tie'):
                a, b = outputs[(case, 'a')], outputs[(case, 'b')]
                store.record(make_ballot(a, b, session=session.id, choice=choice), AT)

        results = close(load_family('f'), store, AT).results

        assert results.scorer == 'votes'
        assert {standing.system for standing in results.standings} == {'a', 'b'}
        assert results.unranked == []

    def test_leaves_the_systems_a_board_cannot_bound_unranked_there(
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

        results = close(load_family('f'), store, AT).results

        assert results.scorer == 'votes'
        assert {standing.system for standing in results.standings} == {'a', 'b', 'c'}
        assert results.unranked == ['d']
        docs = next(board for board in results.categories if board.category == 'docs')
        assert [standing.system for standing in docs.standings] == ['a']
        assert docs.unranked == ['b', 'c']

    def test_marks_a_system_preliminary_below_the_decisive_vote_bar(
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

        results = close(load_family('f'), store, AT).results

        assert results.scorer == 'votes'
        expected = {'x': False, 'y': False, 'z': True}
        assert {s.system: s.preliminary for s in results.standings} == expected
        (board,) = results.categories
        assert {s.system: s.preliminary for s in board.standings} == expected

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

        results = close(load_family('f'), store, AT).results

        assert results.scorer == 'votes'
        assert results.length is not None
        assert results.length > 50

    @pytest.mark.parametrize(
        'texts',
        [
            {'x': '{case} answer', 'y': '{case} reply'},
            {'x': '{case} gives an answer', 'y': '{case}'},
        ],
        ids=['equal', 'constant-ratio'],
    )
    def test_leaves_length_out_when_no_vote_varies_it(
        self, families_dir: Path, store: VoteStore, texts: dict[str, str]
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
            for choice in ('a', 'b', 'tie'):
                x, y = outputs[(case, 'x')], outputs[(case, 'y')]
                store.record(make_ballot(x, y, session=session.id, choice=choice), AT)

        results = close(load_family('f'), store, AT).results

        assert results.scorer == 'votes'
        assert {standing.system for standing in results.standings} == {'x', 'y'}
        assert results.length is None

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

        results = close(load_family('f'), store, AT).results

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

        results = close(load_family('f'), store, AT).results

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

        results = close(family, store, AT).results

        assert results.scorer == 'votes'
        agreement = results.rater_agreement
        assert (agreement.repeats, agreement.agreement) == (2, 0.5)
        assert agreement.below_floor

    def test_anchors_each_category_at_the_board_anchor(
        self, families_dir: Path, store: VoteStore
    ) -> None:
        family = small_family(families_dir, store)

        results = close(family, store, AT).results

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

        results = close(load_family('f'), store, AT).results

        assert results.scorer == 'votes'
        assert (results.reference, results.anchor) == ('large', 'medium')
        assert {standing.system for standing in results.standings} == {
            'medium',
            'small',
            'tiny',
        }
        assert results.unranked == []

    def test_roots_at_the_most_voted_system_beside_a_barely_voted_reference(
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

        results = close(load_family('f'), store, AT).results

        assert results.scorer == 'votes'
        assert (results.reference, results.anchor) == ('ref', 'a')
        assert {standing.system for standing in results.standings} == {'a', 'b'}
        assert results.unranked == ['ref']

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

        results = close(load_family('f'), store, AT).results

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

        results = close(load_family('f'), store, AT).results

        assert results.scorer == 'votes'
        assert results.anchor == 'x'
        docs = next(board for board in results.categories if board.category == 'docs')
        assert {standing.system for standing in docs.standings} == {'x', 'b'}
        assert docs.unranked == ['c', 'd']

    def test_skips_a_category_whose_own_root_cannot_rank_the_anchor(
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

        results = close(load_family('f'), store, AT).results

        assert results.scorer == 'votes'
        assert results.anchor == 'a'
        assert [board.category for board in results.categories] == ['reply']
        (reply,) = results.categories
        assert 'a' in {standing.system for standing in reply.standings}

    def test_needs_votes(self, families_dir: Path, store: VoteStore) -> None:
        cases = {'c1': 'reply'}
        write_votes_family(families_dir, 'f', ['a', 'b'], cases)
        write_passing_outputs(cases, ['a', 'b'])

        with pytest.raises(ValueError, match="family 'f' has no votes yet"):
            close(load_family('f'), store, AT)

    def test_needs_outputs_on_the_current_cases(
        self, families_dir: Path, store: VoteStore
    ) -> None:
        family = small_family(families_dir, store)
        retired = read_outputs('f')[0].model_copy(update={'case': 'gone'})
        write_outputs('f', [retired])

        with pytest.raises(ValueError) as raised:
            close(family, store, AT)
        assert str(raised.value) == (
            "family 'f' has no outputs on its current cases. "
            'Run `uv run bench run f` first.'
        )

    def test_needs_votes_on_the_current_outputs(
        self, families_dir: Path, store: VoteStore
    ) -> None:
        family = small_family(families_dir, store)
        write_outputs(
            'f',
            [
                output
                if output.system == 'large'
                else output.model_copy(update={'gate_failures': [Gate.TRUNCATED]})
                for output in read_outputs('f')
            ],
        )

        with pytest.raises(
            ValueError, match="family 'f' has votes, but none compares two of its"
        ):
            close(family, store, AT)

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

        results = close(load_family('f'), store, AT).results

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

        results = close(family, store, AT).results

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

        results = close(family, store, AT).results

        assert results.scorer == 'votes'
        assert (results.votes, results.dropped_votes) == (15, 0)

    def test_rejects_outputs_under_several_fingerprints(
        self, families_dir: Path, store: VoteStore
    ) -> None:
        family = small_family(families_dir, store)
        outputs = read_outputs('f')
        old = outputs[0].model_copy(update={'trial': 'old', 'fingerprint': 'fp-old'})
        write_outputs('f', [*outputs, old])

        with pytest.raises(ValueError, match="'large' has outputs under several"):
            close(family, store, AT)

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

        with pytest.raises(ValueError, match="'d' has outputs under several"):
            close(load_family('f'), store, AT)

    @pytest.mark.parametrize(
        ('trials', 'message'),
        [
            (
                [{'case': 'retired', 'fingerprint': 'a'}],
                "^family 'smoke' has no outputs on its current cases\\.$",
            ),
            (
                [
                    {'trial': 'a', 'fingerprint': 'a'},
                    {'trial': 'b', 'fingerprint': 'b'},
                ],
                "'large' has outputs under several",
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
            close(load_family('smoke'), store, AT)

    def test_reports_pass_rates_for_a_tests_family(self, store: VoteStore) -> None:
        trials: list[dict[str, Any]] = [
            {'trial': '1', 'reward': 1.0, 'latency_sec': 2.0},
            {'trial': '2', 'reward': 0.0, 'latency_sec': 4.0},
            {
                'trial': '3',
                'error': 'TimeoutError',
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

        results = close(load_family('smoke'), store, AT).results

        assert results.scorer == 'tests'
        (standing,) = results.standings
        assert (standing.trials, standing.errored) == (3, 1)
        assert standing.pass_rate == pytest.approx(1 / 3)
        assert standing.mean_cost_usd == pytest.approx(0.01)
        assert standing.p50_latency_sec == 3.0


class TestWriteReport:
    def test_writes_reports_family_round_json(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(report, 'REPORTS_DIR', tmp_path / 'reports')
        closed = Report.model_validate_json(REPORT_FIXTURE.read_text())

        path = write_report(closed)

        assert path == tmp_path / 'reports' / 'arena' / '2026-10-21.json'
        assert Report.model_validate_json(path.read_text()) == closed


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


class Row(NamedTuple):
    system: str
    score: float
    spread: float
    cost: float | None
    latency: float
    preliminary: bool


class TestDecide:
    @staticmethod
    def board(rows: list[Row]) -> tuple[list[Standing], Ratings]:
        """Build standings and resamples in which spreads pair against each other.

        Each system's resamples run linearly across its spread, so a system with
        spread 100 against a top with spread 0 ties within about 95 points.
        """
        steps = np.linspace(-1, 1, 201)
        scores = np.array([row.score for row in rows])
        standings = [
            Standing(
                system=row.system,
                fingerprint=None,
                score=row.score,
                interval=Interval(
                    lower=row.score - row.spread, upper=row.score + row.spread
                ),
                rank=int((scores > row.score).sum()) + 1,
                best_rank=1,
                worst_rank=len(rows),
                preliminary=row.preliminary,
                votes=50,
                decisive_votes=45,
                both_bad_rate=0.0,
                outputs=10,
                gate_failure_rate=0.0,
                mean_cost_usd=row.cost,
                p50_latency_sec=row.latency,
                output_tokens=None,
                latency_sec=None,
                defects={},
            )
            for row in rows
        ]
        ratings = Ratings(
            systems=tuple(row.system for row in rows),
            score=scores,
            lower=scores,
            upper=scores,
            draws=np.stack([row.score + row.spread * steps for row in rows], axis=1),
            position=0.0,
        )
        return standings, ratings

    @pytest.mark.parametrize(
        ('rows', 'expected'),
        [
            (
                [
                    Row('x', 1100, 0, 0.02, 9.0, False),
                    Row('y', 1000, 0, 0.01, 1.0, False),
                ],
                Decision(leader='x', separated=True, tie_break='score'),
            ),
            (
                [
                    Row('x', 1100, 0, 0.02, 9.0, False),
                    Row('y', 1050, 100, 0.01, 9.0, False),
                    Row('z', 1020, 100, None, 1.0, False),
                    Row('w', 900, 100, 0.001, 1.0, False),
                ],
                Decision(leader='y', separated=False, tie_break='cost'),
            ),
            (
                [
                    Row('x', 1100, 0, 0.01, 9.0, False),
                    Row('y', 1050, 100, 0.01, 3.0, False),
                ],
                Decision(leader='y', separated=False, tie_break='latency'),
            ),
            (
                [
                    Row('x', 1100, 0, 0.01, 3.0, False),
                    Row('y', 1050, 100, 0.01, 3.0, False),
                ],
                Decision(leader='x', separated=False, tie_break='score'),
            ),
            (
                [
                    Row('x', 1100, 0, None, 3.0, False),
                    Row('y', 1050, 100, None, 2.0, False),
                ],
                Decision(leader='y', separated=False, tie_break='latency'),
            ),
            (
                [
                    Row('x', 1100, 0, 0.02, 9.0, False),
                    Row('y', 900, 0, 0.01, 1.0, True),
                ],
                Decision(leader='x', separated=True, tie_break='score'),
            ),
        ],
        ids=[
            'separated',
            'cost',
            'latency',
            'score',
            'unknown-cost',
            'preliminary-outside-the-tie',
        ],
    )
    def test_breaks_a_tie_with_the_top_on_cost_then_latency(
        self, rows: list[Row], expected: Decision
    ) -> None:
        assert decide(*self.board(rows)) == expected

    @pytest.mark.parametrize(
        'rows',
        [
            [Row('x', 1800, 0, 0.001, 1.0, True), Row('y', 1100, 0, 0.02, 9.0, False)],
            [Row('x', 1100, 0, 0.02, 9.0, False), Row('y', 1050, 100, 0.01, 1.0, True)],
        ],
        ids=['preliminary-top', 'preliminary-tied'],
    )
    def test_decides_nothing_while_a_tied_system_is_preliminary(
        self, rows: list[Row]
    ) -> None:
        assert decide(*self.board(rows)) is None
