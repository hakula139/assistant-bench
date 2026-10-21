import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from assistant_bench import report
from assistant_bench.arena.store import Ballot, Choice, Flag, Side, Vote, VoteStore
from assistant_bench.families import Family, Gate, load_family
from assistant_bench.outputs import read_outputs, write_outputs
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
from support import (
    REPORT_FIXTURE,
    SYNTHETIC_CLOSE,
    make_output,
    write_synthetic_round,
    write_votes_family,
)


AT = datetime(2026, 10, 21, 9, tzinfo=UTC)


def approx(value: Any) -> Any:
    """Compare floats in a JSON document within a relative 1e-6."""
    if isinstance(value, float):
        return pytest.approx(value, rel=1e-6, abs=1e-9)
    if isinstance(value, dict):
        return {key: approx(item) for key, item in value.items()}
    if isinstance(value, list):
        return [approx(item) for item in value]
    return value


def small_family(families_dir: Path, store: VoteStore) -> Family:
    """Write family `f` with three systems and a few hand-placed votes."""
    write_votes_family(
        families_dir,
        'f',
        ['large', 'medium', 'small'],
        {'c1': 'reply', 'c2': 'docs', 'c3': 'misc'},
    )
    outputs = [
        make_output(
            trial=f'{case}__{system}',
            job='job-1',
            case=case,
            system=system,
            fingerprint=f'fp-{system}',
            text='one two three' if system == 'large' else '一二三四五六',
            gate_failures=[],
            cost_usd=cost,
            latency_sec=latency,
            output_tokens=None if system == 'small' else 10,
        )
        for case in ('c1', 'c2', 'c3')
        for system, cost, latency in (
            ('large', 0.02, 5.0),
            ('medium', 0.01, 4.0),
            ('small', None, 3.0),
        )
    ]
    write_outputs('f', outputs)
    by_trial = {output.trial: Side.of(output) for output in outputs}
    session = store.start_session('f', AT)
    votes: list[tuple[str, str, str, Choice, frozenset[Flag]]] = [
        ('c1', 'large', 'medium', 'a', frozenset({'length'})),
        ('c1', 'medium', 'small', 'tie', frozenset({'register'})),
        ('c2', 'small', 'large', 'both_bad', frozenset()),
        ('c2', 'large', 'small', 'b', frozenset()),
        ('c3', 'medium', 'small', 'a', frozenset()),
    ]
    for case, left, right, choice, flags in votes:
        store.record(
            Ballot(
                session=session.id,
                case=case,
                a=by_trial[f'{case}__{left}'],
                b=by_trial[f'{case}__{right}'],
                choice=choice,
                flags=flags,
                duration_sec=10.0,
            ),
            AT,
        )
    return load_family('f')


class TestClose:
    def test_matches_the_committed_fixture(
        self, families_dir: Path, store: VoteStore
    ) -> None:
        write_synthetic_round(families_dir, store)

        closed = close(load_family('arena'), store, SYNTHETIC_CLOSE)

        written = json.loads(closed.model_dump_json())
        committed = json.loads(REPORT_FIXTURE.read_text())
        if committed != approx(written):
            REPORT_FIXTURE.write_text(f'{closed.model_dump_json(indent=2)}\n')
            pytest.fail(
                f'{REPORT_FIXTURE.name} was stale and is rewritten, so review it'
            )

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
        assert both_bad == {'large': 1 / 3, 'medium': 0.0, 'small': 1 / 4}
        large_small = next(
            row
            for row in results.head_to_head
            if (row.system, row.opponent) == ('large', 'small')
        )
        assert (large_small.wins, large_small.losses, large_small.both_bad) == (0, 1, 1)
        assert large_small.win_rate == 0.25
        small = next(s for s in results.standings if s.system == 'small')
        assert small.output_tokens is None
        assert small.mean_cost_usd is None

    def test_fits_the_results_that_gate_failures_settle(
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
        assert small_after.votes == small_before.votes == 4
        assert small_after.gate_failure_rate == 1.0

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
            assert reference.score == 1000

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

    def test_rejects_votes_under_several_fingerprints(
        self, families_dir: Path, store: VoteStore
    ) -> None:
        family = small_family(families_dir, store)
        (vote, *_) = store.votes('f')
        old = vote.a.model_copy(update={'fingerprint': 'fp-old'})
        store.record(
            Ballot(
                session=vote.session,
                case='c1',
                a=old,
                b=vote.b,
                choice='a',
                duration_sec=10.0,
            ),
            AT,
        )

        with pytest.raises(ValueError, match="'large' has votes under several"):
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


class TestReport:
    def test_committed_fixture_validates_against_the_json_schema(self) -> None:
        schema = Report.model_json_schema(mode='serialization')

        jsonschema.validate(json.loads(REPORT_FIXTURE.read_text()), schema)


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


class TestDecide:
    @staticmethod
    def standing(
        system: str, rank: int, best_rank: int, cost: float | None, latency: float
    ) -> Standing:
        return Standing(
            system=system,
            fingerprint=None,
            score=1000.0 - rank,
            interval=Interval(lower=900.0, upper=1100.0),
            rank=rank,
            best_rank=best_rank,
            worst_rank=3,
            preliminary=False,
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

    @pytest.mark.parametrize(
        ('rows', 'expected'),
        [
            (
                [('x', 1, 1, 0.02, 9.0), ('y', 2, 2, 0.01, 1.0)],
                Decision(leader='x', separated=True, tie_break='score'),
            ),
            (
                [
                    ('x', 1, 1, 0.02, 9.0),
                    ('y', 2, 1, 0.01, 9.0),
                    ('z', 3, 1, None, 1.0),
                ],
                Decision(leader='y', separated=False, tie_break='cost'),
            ),
            (
                [('x', 1, 1, 0.01, 9.0), ('y', 2, 1, 0.01, 3.0)],
                Decision(leader='y', separated=False, tie_break='latency'),
            ),
            (
                [('x', 1, 1, 0.01, 3.0), ('y', 2, 1, 0.01, 3.0)],
                Decision(leader='x', separated=False, tie_break='score'),
            ),
            (
                [('x', 1, 1, None, 3.0), ('y', 2, 1, None, 2.0)],
                Decision(leader='y', separated=False, tie_break='latency'),
            ),
        ],
    )
    def test_breaks_a_tie_on_cost_then_latency(
        self, rows: list[tuple[str, int, int, float | None, float]], expected: Decision
    ) -> None:
        assert decide([self.standing(*row) for row in rows]) == expected
