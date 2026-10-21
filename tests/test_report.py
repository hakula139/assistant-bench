import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import jsonschema
import numpy as np
import pytest

from assistant_bench import report
from assistant_bench.arena.service import Arena, load_families
from assistant_bench.arena.store import Ballot, Choice, Flag, Side, Vote, VoteStore
from assistant_bench.families import Family, Gate, load_family
from assistant_bench.outputs import Output, read_outputs, write_outputs
from assistant_bench.report import (
    Decision,
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
    EARLY_REPORT_FIXTURE,
    RENDER_ORIGIN,
    REPORT_FIXTURE,
    SYNTHETIC_CLOSE,
    SYNTHETIC_EARLY_CLOSE,
    Clock,
    make_arena,
    make_output,
    write_synthetic_round,
    write_votes_family,
)


AT = datetime(2026, 10, 21, 9, tzinfo=UTC)
ROUNDS = pytest.mark.parametrize(
    ('fixture', 'sessions', 'closed_at'),
    [
        (REPORT_FIXTURE, 10, SYNTHETIC_CLOSE),
        (EARLY_REPORT_FIXTURE, 1, SYNTHETIC_EARLY_CLOSE),
    ],
    ids=['full', 'early'],
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


def record(
    store: VoteStore,
    session: str,
    a: Output,
    b: Output,
    choice: Choice,
    flags: frozenset[Flag] = frozenset(),
) -> Vote:
    return store.record(
        Ballot(
            session=session,
            case=a.case,
            a=Side.of(a),
            b=Side.of(b),
            choice=choice,
            flags=flags,
            duration_sec=10.0,
        ),
        AT,
    )


def passing_outputs(
    cases: dict[str, str], texts: dict[str, dict[str, str]]
) -> dict[tuple[str, str], Output]:
    """Write one passing output per case and system, `texts[case][system]`."""
    outputs = {
        (case, system): make_output(
            trial=f'{case}__{system}',
            job='job-1',
            case=case,
            system=system,
            fingerprint=f'fp-{system}',
            text=text,
            gate_failures=[],
        )
        for case in cases
        for system, text in texts[case].items()
    }
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
    texts = {'large': 'one two three', 'medium': '一二三四五六', 'small': 'four five'}
    outputs = [
        make_output(
            trial=f'{case}__{system}',
            job='job-1',
            case=case,
            system=system,
            fingerprint=f'fp-{system}',
            text=texts[system],
            gate_failures=[],
            cost_usd=cost,
            latency_sec=latency,
            output_tokens=None if system == 'small' else 10,
        )
        for case in cases
        for system, cost, latency in (
            ('large', 0.02, 5.0),
            ('medium', 0.01, 4.0),
            ('small', None, 3.0),
        )
    ]
    write_outputs('f', outputs)
    by_trial = {output.trial: output for output in outputs}
    session = store.start_session('f', AT)
    votes: list[tuple[str, str, str, Choice, frozenset[Flag]]] = [
        ('c1', 'large', 'medium', 'a', frozenset({'length'})),
        ('c1', 'medium', 'small', 'tie', frozenset({'register'})),
        ('c2', 'small', 'large', 'both_bad', frozenset()),
        ('c2', 'large', 'small', 'b', frozenset()),
        ('c3', 'medium', 'small', 'a', frozenset()),
    ]
    for case in ties:
        votes += [
            (case, 'large', 'medium', 'tie', frozenset()),
            (case, 'small', 'large', 'tie', frozenset()),
        ]
    for case, left, right, choice, flags in votes:
        record(
            store,
            session.id,
            by_trial[f'{case}__{left}'],
            by_trial[f'{case}__{right}'],
            choice,
            flags,
        )
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


class TestClose:
    @ROUNDS
    def test_matches_the_committed_fixture(
        self,
        families_dir: Path,
        store: VoteStore,
        fixture: Path,
        sessions: int,
        closed_at: datetime,
    ) -> None:
        write_synthetic_round(families_dir, store, sessions)

        closed = close(load_family('arena'), store, closed_at)

        written = json.loads(closed.model_dump_json())
        if json.loads(fixture.read_text()) != approx(written):
            fixture.write_text(f'{closed.model_dump_json(indent=2)}\n')
            pytest.fail(f'{fixture.name} was stale and is rewritten, so review it')

    def test_reports_aggregates_under_public_names_only(
        self, families_dir: Path, store: VoteStore
    ) -> None:
        family = small_family(families_dir, store)

        closed = close(family, store, AT).model_dump_json()

        for private in ('c1', 'c3', 'job-1', '__', 'Write about', '一二三'):
            assert private not in closed

    def test_tallies_flags_against_the_side_a_vote_did_not_prefer(
        self, families_dir: Path, store: VoteStore
    ) -> None:
        family = small_family(families_dir, store)

        results = close(family, store, AT).results

        assert results.scorer == 'votes'
        flags = {standing.system: standing.flags for standing in results.standings}
        assert flags['large']['length'] == 0
        assert flags['medium']['length'] == 1
        assert flags['medium']['register'] == flags['small']['register'] == 1
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
            view = arena.vote(view.pair.token, 'a', frozenset(), None)
        cases = families_dir / 'f' / 'cases.jsonl'
        cases.write_text(cases.read_text().replace('"c1"', '"c1-v2"'))
        restarted = Arena(
            store, load_families(), RENDER_ORIGIN, clock, np.random.default_rng(0)
        )

        view = restarted.start('f')
        shown = []
        while view.pair is not None:
            shown.append(view.pair.instruction)
            view = restarted.vote(view.pair.token, 'a', frozenset(), None)
        results = close(load_family('f'), store, AT).results

        assert sorted(shown) == ['Write about c2.', 'Write about c3.']
        assert results.scorer == 'votes'
        assert (results.votes, results.dropped_votes) == (4, 1)

    def test_ranks_only_the_systems_connected_to_the_reference(
        self, families_dir: Path, store: VoteStore
    ) -> None:
        cases = {'c1': 'reply', 'c2': 'reply'}
        write_votes_family(families_dir, 'f', ['a', 'b', 'c', 'd'], cases)
        outputs = passing_outputs(
            cases, {case: {s: f'{s} on {case}' for s in 'abcd'} for case in cases}
        )
        session = store.start_session('f', AT)
        for left, right, case in (('a', 'b', 'c1'), ('c', 'd', 'c2')):
            for choice in ('a', 'b', 'a'):
                record(
                    store,
                    session.id,
                    outputs[(case, left)],
                    outputs[(case, right)],
                    choice,
                )

        results = close(load_family('f'), store, AT).results

        assert results.scorer == 'votes'
        assert {standing.system for standing in results.standings} == {'a', 'b'}
        assert results.unranked == ['c', 'd']
        (board,) = results.categories
        assert {standing.system for standing in board.standings} == {'a', 'b'}
        assert board.unranked == ['c', 'd']

    def test_measures_a_preference_for_longer_outputs(
        self, families_dir: Path, store: VoteStore
    ) -> None:
        cases = {f'c{i}': 'reply' for i in range(8)}
        write_votes_family(families_dir, 'f', ['x', 'y'], cases)
        words = {'x': lambda i: 5 + 3 * i, 'y': lambda i: 26 - 3 * i}
        outputs = passing_outputs(
            cases,
            {
                f'c{i}': {s: ' '.join(['word'] * words[s](i)) for s in 'xy'}
                for i in range(8)
            },
        )
        session = store.start_session('f', AT)
        for i in range(8):
            x, y = outputs[(f'c{i}', 'x')], outputs[(f'c{i}', 'y')]
            longer: Choice = 'a' if i >= 4 else 'b'
            record(store, session.id, x, y, longer)
            record(store, session.id, y, x, 'b' if longer == 'a' else 'a')
        record(store, session.id, outputs[('c0', 'x')], outputs[('c0', 'y')], 'a')

        results = close(load_family('f'), store, AT).results

        assert results.scorer == 'votes'
        assert results.length is not None
        assert results.length > 50

    def test_agrees_across_halves_split_by_session_parity(
        self, families_dir: Path, store: VoteStore
    ) -> None:
        write_votes_family(families_dir, 'f', ['a', 'b', 'c'], {'c1': 'reply'})
        outputs = passing_outputs({'c1': 'reply'}, {'c1': {s: s for s in 'abc'}})
        strong = [('a', 'b'), ('b', 'c'), ('a', 'c')] * 3
        weak = [('c', 'b'), ('b', 'a'), ('c', 'a')]
        for day, pairs in enumerate((strong, strong, weak, weak)):
            session = store.start_session('f', AT + timedelta(days=day))
            for i, (winner, loser) in enumerate(pairs):
                a, b = outputs[('c1', winner)], outputs[('c1', loser)]
                if i % 2:
                    record(store, session.id, b, a, 'b')
                else:
                    record(store, session.id, a, b, 'a')

        results = close(load_family('f'), store, AT).results

        assert results.scorer == 'votes'
        assert results.split_half_tau == pytest.approx(1.0)

    def test_leaves_split_half_agreement_out_when_a_half_misses_the_reference(
        self, families_dir: Path, store: VoteStore
    ) -> None:
        write_votes_family(families_dir, 'f', ['a', 'b', 'c'], {'c1': 'reply'})
        outputs = passing_outputs({'c1': 'reply'}, {'c1': {s: s for s in 'abc'}})
        for day, pairs in enumerate(([('a', 'b'), ('a', 'c')], [('b', 'c')] * 2)):
            session = store.start_session('f', AT + timedelta(days=day))
            for winner, loser in pairs:
                record(
                    store, session.id, outputs['c1', winner], outputs['c1', loser], 'a'
                )

        results = close(load_family('f'), store, AT).results

        assert results.scorer == 'votes'
        assert results.split_half_tau is None

    def test_measures_rater_agreement_on_swapped_repeats(
        self, families_dir: Path, store: VoteStore
    ) -> None:
        family = small_family(families_dir, store)
        first, _, third, *_ = store.votes('f')
        session = store.start_session('f', AT)
        repeats: list[tuple[Vote, Choice]] = [(first, 'b'), (third, 'a')]
        for original, choice in repeats:
            store.record(
                Ballot(
                    session=session.id,
                    case=original.case,
                    a=original.b,
                    b=original.a,
                    choice=choice,
                    duration_sec=10.0,
                    repeat_of=original.id,
                ),
                AT,
            )

        results = close(family, store, AT).results

        assert results.scorer == 'votes'
        agreement = results.rater_agreement
        assert (agreement.repeats, agreement.agreement) == (2, 0.5)
        assert agreement.below_floor

    def test_anchors_each_category_at_the_reference(
        self, families_dir: Path, store: VoteStore
    ) -> None:
        family = small_family(families_dir, store)

        results = close(family, store, AT).results

        assert results.scorer == 'votes'
        assert [board.category for board in results.categories] == ['docs', 'reply']
        for board in results.categories:
            reference = next(s for s in board.standings if s.system == 'large')
            assert reference.score == pytest.approx(1000)

    @pytest.mark.parametrize('reference', ['missing', None])
    def test_needs_votes_on_the_reference(
        self, families_dir: Path, store: VoteStore, reference: str | None
    ) -> None:
        family = small_family(families_dir, store)
        if reference is None:
            family = family.model_copy(update={'name': 'quiet'})
            message = "family 'quiet' has no votes"
        else:
            settings = family.settings.model_copy(update={'reference': reference})
            family = family.model_copy(update={'settings': settings})
            message = "reference 'missing' has no votes"

        with pytest.raises(ValueError, match=message):
            close(family, store, AT)

    def test_counts_the_reference_votes_it_dropped(
        self, families_dir: Path, store: VoteStore
    ) -> None:
        family = small_family(families_dir, store)
        write_outputs('f', [o for o in read_outputs('f') if o.system != 'large'])

        message = r"reference 'large' has no votes on its current outputs \(13 dropped"
        with pytest.raises(ValueError, match=message):
            close(family, store, AT)

    def test_rejects_outputs_under_several_fingerprints(
        self, families_dir: Path, store: VoteStore
    ) -> None:
        family = small_family(families_dir, store)
        outputs = read_outputs('f')
        old = outputs[0].model_copy(update={'trial': 'old', 'fingerprint': 'fp-old'})
        write_outputs('f', [*outputs, old])

        with pytest.raises(ValueError, match="'large' has outputs under several"):
            close(family, store, AT)

    @pytest.mark.parametrize(
        ('fingerprints', 'message'),
        [([], 'has no outputs'), (['a', 'b'], 'has outputs under several')],
    )
    def test_rejects_a_tests_family_it_cannot_aggregate(
        self, store: VoteStore, fingerprints: list[str], message: str
    ) -> None:
        write_outputs(
            'smoke',
            [make_output(trial=f, system='large', fingerprint=f) for f in fingerprints],
        )

        with pytest.raises(ValueError, match=message):
            close(load_family('smoke'), store, AT)

    def test_reports_pass_rates_for_a_tests_family(self, store: VoteStore) -> None:
        write_outputs(
            'smoke',
            [
                make_output(trial='1', system='large', reward=1.0, latency_sec=2.0),
                make_output(trial='2', system='large', reward=0.0, latency_sec=4.0),
                make_output(trial='3', system='large', error='TimeoutError'),
            ],
        )

        results = close(load_family('smoke'), store, AT).results

        assert results.scorer == 'tests'
        (standing,) = results.standings
        assert (standing.trials, standing.errored) == (3, 1)
        assert standing.pass_rate == pytest.approx(1 / 3)
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
            ('', 0),
        ],
    )
    def test_counts_han_characters_and_other_words(
        self, text: str, length: int
    ) -> None:
        assert text_length(text) == length


type Row = tuple[str, float, float, float | None, float, bool]
"""A system, its score, how far its resamples spread, cost, latency, preliminary."""


class TestDecide:
    @staticmethod
    def board(rows: list[Row]) -> tuple[list[Standing], Ratings]:
        """Build standings and resamples in which spreads pair against each other.

        Each system's resamples run linearly across its spread, so a system with
        spread 100 against a top with spread 0 ties within about 95 points.
        """
        steps = np.linspace(-1, 1, 201)
        scores = np.array([row[1] for row in rows])
        standings = [
            Standing(
                system=system,
                fingerprint=None,
                score=score,
                interval=Interval(lower=score - spread, upper=score + spread),
                rank=int((scores > score).sum()) + 1,
                best_rank=1,
                worst_rank=len(rows),
                preliminary=preliminary,
                votes=50,
                decisive_votes=45,
                both_bad_rate=0.0,
                outputs=10,
                gate_failure_rate=0.0,
                mean_cost_usd=cost,
                p50_latency_sec=latency,
                output_tokens=None,
                latency_sec=None,
                flags={},
            )
            for system, score, spread, cost, latency, preliminary in rows
        ]
        ratings = Ratings(
            systems=tuple(row[0] for row in rows),
            score=scores,
            lower=scores,
            upper=scores,
            draws=np.stack([row[1] + row[2] * steps for row in rows], axis=1),
            position=0.0,
        )
        return standings, ratings

    @pytest.mark.parametrize(
        ('rows', 'expected'),
        [
            (
                [('x', 1100, 0, 0.02, 9.0, False), ('y', 1000, 0, 0.01, 1.0, False)],
                Decision(leader='x', separated=True, tie_break='score'),
            ),
            (
                [
                    ('x', 1100, 0, 0.02, 9.0, False),
                    ('y', 1050, 100, 0.01, 9.0, False),
                    ('z', 1020, 100, None, 1.0, False),
                    ('w', 900, 100, 0.001, 1.0, False),
                ],
                Decision(leader='y', separated=False, tie_break='cost'),
            ),
            (
                [('x', 1100, 0, 0.01, 9.0, False), ('y', 1050, 100, 0.01, 3.0, False)],
                Decision(leader='y', separated=False, tie_break='latency'),
            ),
            (
                [('x', 1100, 0, 0.01, 3.0, False), ('y', 1050, 100, 0.01, 3.0, False)],
                Decision(leader='x', separated=False, tie_break='score'),
            ),
            (
                [('x', 1100, 0, None, 3.0, False), ('y', 1050, 100, None, 2.0, False)],
                Decision(leader='y', separated=False, tie_break='latency'),
            ),
            (
                [('x', 1100, 0, 0.02, 9.0, False), ('y', 900, 0, 0.01, 1.0, True)],
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
            [('x', 1800, 0, 0.001, 1.0, True), ('y', 1100, 0, 0.02, 9.0, False)],
            [('x', 1100, 0, 0.02, 9.0, False), ('y', 1050, 100, 0.01, 1.0, True)],
        ],
        ids=['preliminary-top', 'preliminary-tied'],
    )
    def test_decides_nothing_while_a_tied_system_is_preliminary(
        self, rows: list[Row]
    ) -> None:
        assert decide(*self.board(rows)) is None
