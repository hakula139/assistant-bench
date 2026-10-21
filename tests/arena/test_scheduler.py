from collections import Counter
from datetime import UTC, datetime, timedelta
from itertools import permutations

import numpy as np
import pytest

from assistant_bench.arena import scheduler
from assistant_bench.arena.scheduler import Draw, Pool, draw
from assistant_bench.arena.store import Choice, Side, Vote
from assistant_bench.families import Gate
from assistant_bench.outputs import Output
from support import make_output


NOW = datetime(2026, 10, 21, 9, tzinfo=UTC)
SYSTEMS = ('large', 'medium', 'small')


def make_pool(
    systems: tuple[str, ...] = SYSTEMS, cases: int = 10, **fields: object
) -> Pool:
    outputs = [
        make_output(
            trial=f'c{case}__{system}',
            case=f'c{case}',
            system=system,
            text=f'{system} on c{case}',
            gate_failures=[],
            **fields,
        )
        for case in range(cases)
        for system in systems
    ]
    return Pool.build(systems, outputs)


def vote_on(
    picked: Draw, choice: Choice, vote_id: int, at: datetime = NOW, **fields: object
) -> Vote:
    return Vote.model_validate(
        {
            'id': vote_id,
            'recorded_at': at,
            'session': 's',
            'case': picked.case,
            'a': Side.of(picked.a),
            'b': Side.of(picked.b),
            'choice': choice,
        }
        | fields
    )


def votes_between(
    pool: Pool, left: str, right: str, choices: list[Choice], start: int = 1
) -> list[Vote]:
    return [
        vote_on(
            Draw(
                case=f'c{i % 10}',
                a=pool.outputs_by_case[f'c{i % 10}'][left][0],
                b=pool.outputs_by_case[f'c{i % 10}'][right][0],
            ),
            choice,
            start + i,
        )
        for i, choice in enumerate(choices)
    ]


class TestPool:
    class TestBuild:
        def test_keeps_the_gated_outputs_of_listed_systems(self) -> None:
            outputs: list[Output] = [
                make_output(trial='1', system='large', text='Hi.', gate_failures=[]),
                make_output(
                    trial='2', system='large', text='', gate_failures=[Gate.EMPTY]
                ),
                make_output(trial='3', system='large', error='TimeoutError'),
                make_output(trial='4', system='retired', gate_failures=[]),
            ]

            pool = Pool.build(['large', 'small'], outputs)

            (by_system,) = pool.outputs_by_case.values()
            assert list(by_system) == ['large']
            assert [output.trial for output in by_system['large']] == ['1', '2']


class TestDraw:
    def test_seeds_every_ordered_pair_before_adapting(self) -> None:
        pool = make_pool()
        rng = np.random.default_rng(0)
        votes: list[Vote] = []

        for vote_id in range(1, 13):
            picked = draw(pool, votes, set(), NOW, rng)
            assert picked is not None
            votes.append(vote_on(picked, 'a', vote_id))

        counts = Counter((vote.a.system, vote.b.system) for vote in votes)
        assert counts == dict.fromkeys(permutations(SYSTEMS, 2), 2)

    def test_draws_only_cases_the_session_has_not_used(self) -> None:
        pool = make_pool(cases=3)
        rng = np.random.default_rng(0)

        cases = set()
        for _ in range(10):
            picked = draw(pool, [], {'c0', 'c2'}, NOW, rng)
            assert picked is not None
            cases.add(picked.case)

        assert cases == {'c1'}
        assert draw(pool, [], {'c0', 'c1', 'c2'}, NOW, rng) is None

    def test_favors_the_close_pair_once_seeded(self) -> None:
        pool = make_pool()
        votes = (
            votes_between(pool, 'large', 'medium', ['a'] * 10)
            + votes_between(pool, 'medium', 'large', ['b'] * 10, start=11)
            + votes_between(pool, 'large', 'small', ['a'] * 10, start=21)
            + votes_between(pool, 'small', 'large', ['b'] * 10, start=31)
            + votes_between(pool, 'medium', 'small', ['a', 'b'] * 2, start=41)
            + votes_between(pool, 'small', 'medium', ['a', 'b'] * 2, start=45)
        )
        rng = np.random.default_rng(0)

        pairs = set()
        for _ in range(10):
            picked = draw(pool, votes, set(), NOW, rng)
            assert picked is not None
            pairs.add(frozenset((picked.a.system, picked.b.system)))

        assert pairs == {frozenset(('medium', 'small'))}

    def test_puts_the_less_shown_order_on_the_left(self) -> None:
        pool = make_pool(systems=('large', 'small'))
        votes = votes_between(pool, 'large', 'small', ['a', 'b', 'tie']) + (
            votes_between(pool, 'small', 'large', ['a', 'b'], start=4)
        )
        rng = np.random.default_rng(0)

        picked = draw(pool, votes, set(), NOW, rng)

        assert picked is not None
        assert (picked.a.system, picked.b.system) == ('small', 'large')

    @pytest.mark.parametrize(
        ('age', 'repeated', 'expected'),
        [
            (timedelta(days=1), False, True),
            (timedelta(hours=23), False, False),
            (timedelta(days=2), True, False),
        ],
    )
    def test_repeats_a_vote_a_day_old_with_sides_swapped(
        self,
        monkeypatch: pytest.MonkeyPatch,
        age: timedelta,
        repeated: bool,
        expected: bool,
    ) -> None:
        monkeypatch.setattr(scheduler, 'REPEAT_SHARE', 1.0)
        pool = make_pool(systems=('large', 'small'))
        (original,) = votes_between(pool, 'large', 'small', ['a'])
        original = original.model_copy(update={'recorded_at': NOW - age})
        votes = [original]
        if repeated:
            votes.append(
                original.model_copy(
                    update={'id': 2, 'a': original.b, 'b': original.a, 'repeat_of': 1}
                )
            )

        picked = draw(pool, votes, set(), NOW, np.random.default_rng(0))

        assert picked is not None
        assert (picked.repeat_of == 1) is expected
        if expected:
            assert (picked.case, Side.of(picked.a), Side.of(picked.b)) == (
                original.case,
                original.b,
                original.a,
            )

    def test_skips_a_repeat_whose_output_left_the_pool(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(scheduler, 'REPEAT_SHARE', 1.0)
        pool = make_pool(systems=('large', 'small'))
        (original,) = votes_between(pool, 'large', 'small', ['a'])
        original = original.model_copy(
            update={
                'recorded_at': NOW - timedelta(days=3),
                'b': original.b.model_copy(update={'system': 'retired'}),
            }
        )

        picked = draw(pool, [original], set(), NOW, np.random.default_rng(0))

        assert picked is not None
        assert picked.repeat_of is None

    def test_never_repeats_a_resolved_pair(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(scheduler, 'REPEAT_SHARE', 1.0)
        pool = make_pool(systems=('large', 'small'))
        (resolved,) = votes_between(pool, 'large', 'small', ['tie'])
        resolved = resolved.model_copy(
            update={'recorded_at': NOW - timedelta(days=3), 'auto': 'identical'}
        )

        picked = draw(pool, [resolved], set(), NOW, np.random.default_rng(0))

        assert picked is not None
        assert picked.repeat_of is None
