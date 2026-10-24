import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from assistant_bench.arena import scheduler
from assistant_bench.arena.service import (
    Arena,
    NothingToUndo,
    UnknownItem,
    UnlistedDefects,
    load_families,
)
from assistant_bench.arena.store import Ballot, Side, Vote, VoteStore
from assistant_bench.families import Gate
from assistant_bench.outputs import read_outputs, write_outputs
from assistant_bench_arena.models import (
    Defects,
    DiffPane,
    HtmlPane,
    PairView,
    Pane,
    TextPane,
)
from support import (
    ARENA_CASES,
    ARENA_SYSTEMS,
    RENDER_ORIGIN,
    Clock,
    Refits,
    make_arena,
    make_output,
    passing_outputs,
    write_task_family,
    write_votes_family,
)


TASKS = ('t1', 't2', 't3')
TASK_SYSTEMS = ('large', 'medium', 'small')
FAILING_SOLUTIONS = {('t2', 'medium'), ('t3', 'medium'), ('t3', 'small')}


def solution(task: str, system: str) -> str:
    return f'--- a/{task}.py\n+++ b/{task}.py\n@@ -1 +1 @@\n-old\n+{system}\n'


def task_arena(families_dir: Path, clock: Clock, store: VoteStore) -> Arena:
    """Serve task family `f`, where only `large` solves `t3` and two solve `t2`."""
    write_task_family(families_dir, *TASKS, systems=TASK_SYSTEMS)
    failed = {'reward': 0.0, 'gate_failures': [Gate.TESTS]}
    write_outputs(
        'f',
        [
            output.model_copy(
                update={'text': solution(*slot), 'reward': 1.0}
                | (failed if slot in FAILING_SOLUTIONS else {})
            )
            for slot, output in passing_outputs(TASKS, TASK_SYSTEMS).items()
        ],
    )
    return Arena(store, load_families(), RENDER_ORIGIN, clock, np.random.default_rng(0))


def documents(pair: PairView) -> list[str]:
    """Return the document tokens behind a pair's HTML panes."""
    return [
        pane.url.removeprefix(f'{RENDER_ORIGIN}/outputs/')
        for pane in (pair.a, pair.b)
        if isinstance(pane, HtmlPane)
    ]


class TestLoadFamilies:
    def test_serves_families_scored_by_votes_with_outputs(
        self, families_dir: Path
    ) -> None:
        write_votes_family(families_dir, 'empty', ARENA_SYSTEMS, ARENA_CASES)
        write_votes_family(families_dir, 'f', ARENA_SYSTEMS, ARENA_CASES)
        (families_dir / 'tests').mkdir()
        (families_dir / 'tests' / 'family.toml').write_text(
            'scorer = "tests"\ndisplay_name = "Tests"\nsamples = 1\n'
        )
        (families_dir / 'tests' / 'tasks').mkdir()
        write_outputs(
            'f', [make_output(case='c1', system='large', text='Hi.', gate_failures=[])]
        )

        families = load_families()

        assert list(families) == ['f']
        assert families['f'].checklists == {'c1': ['Stays on topic']}

    @pytest.mark.parametrize('installed', ['stale', 'missing'])
    def test_shows_the_current_case_instruction_without_requiring_a_rendered_task(
        self, families_dir: Path, data_dir: Path, installed: str
    ) -> None:
        write_votes_family(families_dir, 'f', ARENA_SYSTEMS, ARENA_CASES)
        instruction = data_dir / 'tasks' / 'f' / 'c1' / 'instruction.md'
        if installed == 'stale':
            instruction.write_text('A previous instruction.')
        else:
            instruction.unlink()
        write_outputs(
            'f', [make_output(case='c1', system='large', text='Hi.', gate_failures=[])]
        )

        families = load_families()

        assert families['f'].instructions == {'c1': 'Write about c1.'}


class TestArena:
    def test_reads_each_transition_from_one_fresh_vote_snapshot(
        self,
        families_dir: Path,
        clock: Clock,
        store: VoteStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        arena = make_arena(families_dir, clock, store)
        read_votes = store.votes
        reads: list[str] = []

        def snapshot(family: str) -> list[Vote]:
            reads.append(family)
            return read_votes(family)

        monkeypatch.setattr(store, 'votes', snapshot)

        first = arena.start('f')

        assert first.pair is not None
        assert (first.votes, first.remaining, reads) == (0, len(ARENA_CASES), ['f'])
        reads.clear()

        resumed = arena.resume(first.id)

        assert resumed == first
        assert reads == ['f']
        reads.clear()

        second = arena.vote(first.pair.token, 'a', Defects(), None)

        assert second.pair is not None
        assert second.pair.instruction != first.pair.instruction
        assert (second.votes, second.remaining, reads) == (
            1,
            len(ARENA_CASES) - 1,
            ['f'],
        )
        reads.clear()

        undone = arena.undo(first.id)

        assert undone.pair is not None
        assert (undone.pair.a, undone.pair.b) == (first.pair.a, first.pair.b)
        assert (undone.votes, undone.remaining, reads) == (0, len(ARENA_CASES), ['f'])

    class TestStart:
        def test_shows_a_pair_that_names_no_system(
            self, families_dir: Path, clock: Clock, store: VoteStore
        ) -> None:
            arena = make_arena(families_dir, clock, store)

            view = arena.start('f')

            assert view.pair is not None
            assert (view.votes, view.remaining) == (0, 3)
            assert view.pair.instruction.startswith('Write about c')
            exposed = view.model_dump_json()
            trials = [
                f'{case}__{system}' for case in ARENA_CASES for system in ARENA_SYSTEMS
            ]
            fingerprints = [f'fp-{system}' for system in ARENA_SYSTEMS]
            for hidden in (*trials, *fingerprints, 'job-1', 'large"', 'small"'):
                assert hidden not in exposed

        @pytest.mark.parametrize(
            ('options'),
            [
                {'cut_off': ('small',)},
                {'cut_off': ('large', 'small')},
                {'texts': dict.fromkeys(ARENA_SYSTEMS, 'Same.')},
            ],
            ids=['one-fails', 'both-fail', 'identical'],
        )
        def test_never_shows_a_pair_the_outputs_settle(
            self,
            families_dir: Path,
            clock: Clock,
            store: VoteStore,
            options: dict[str, Any],
        ) -> None:
            arena = make_arena(families_dir, clock, store, **options)

            view = arena.start('f')

            assert (view.votes, view.remaining, view.pair) == (0, 0, None)
            assert store.votes('f') == []

        def test_shows_only_passing_solutions_as_diffs(
            self, families_dir: Path, clock: Clock, store: VoteStore
        ) -> None:
            arena = task_arena(families_dir, clock, store)
            passing = {
                solution(task, system)
                for task in TASKS
                for system in TASK_SYSTEMS
                if (task, system) not in FAILING_SOLUTIONS
            }

            view = arena.start('f')
            remaining = view.remaining
            shown: list[tuple[Pane, Pane]] = []
            while view.pair is not None:
                shown.append((view.pair.a, view.pair.b))
                view = arena.vote(view.pair.token, 'a', Defects(), None)

            assert remaining == 2
            assert view.votes == 2
            for a, b in shown:
                assert isinstance(a, DiffPane)
                assert isinstance(b, DiffPane)
                assert {a.text, b.text} <= passing
                assert a.text.split('\n')[0] == b.text.split('\n')[0]

        def test_pairs_the_systems_with_outputs_until_the_others_join(
            self, families_dir: Path, clock: Clock, store: VoteStore
        ) -> None:
            cases = {f'c{i}': 'reply' for i in range(8)}
            write_votes_family(families_dir, 'f', ['ref', *ARENA_SYSTEMS], cases)
            write_outputs('f', list(passing_outputs(cases, ARENA_SYSTEMS).values()))
            arena = Arena(store, load_families(), RENDER_ORIGIN, clock)
            view = arena.start('f')
            while view.pair is not None:
                view = arena.vote(view.pair.token, 'a', Defects(), None)
            joined = passing_outputs(cases, ['ref', *ARENA_SYSTEMS])
            write_outputs('f', list(joined.values()))
            clock.now += timedelta(hours=1)
            arena = Arena(store, load_families(), RENDER_ORIGIN, clock)

            joining = arena.start('f')
            assert joining.pair is not None
            arena.vote(joining.pair.token, 'a', Defects(), None)

            *earlier, last = store.votes('f')
            assert len(earlier) == len(cases)
            assert {frozenset(vote.systems) for vote in earlier} == {
                frozenset(ARENA_SYSTEMS)
            }
            assert 'ref' in last.systems

        def test_refits_the_board_when_a_session_starts(
            self,
            families_dir: Path,
            clock: Clock,
            store: VoteStore,
            monkeypatch: pytest.MonkeyPatch,
            fits: Refits,
        ) -> None:
            arena = make_arena(families_dir, clock, store)
            seeded = arena.start('f')
            assert seeded.pair is not None
            arena.vote(seeded.pair.token, 'a', Defects(), None)
            monkeypatch.setattr(scheduler, 'SEED_VOTES_PER_SIDE', 0)

            view = arena.start('f')
            assert view.pair is not None
            arena.vote(view.pair.token, 'a', Defects(), None)
            arena.start('f')

            assert fits.comparisons == [1, 2]

        def test_roots_a_tied_board_by_the_family_s_order(
            self,
            families_dir: Path,
            clock: Clock,
            store: VoteStore,
            monkeypatch: pytest.MonkeyPatch,
            fits: Refits,
        ) -> None:
            write_votes_family(families_dir, 'f', ['small', 'large'], ARENA_CASES)
            outputs = passing_outputs(ARENA_CASES, ['small', 'large'])
            write_outputs('f', list(outputs.values()))
            arena = Arena(store, load_families(), RENDER_ORIGIN, clock)
            seeded = arena.start('f')
            assert seeded.pair is not None
            arena.vote(seeded.pair.token, 'a', Defects(), None)
            monkeypatch.setattr(scheduler, 'SEED_VOTES_PER_SIDE', 0)

            arena.start('f')

            assert fits.roots == ['small']

        def test_refuses_a_family_it_does_not_serve(
            self, families_dir: Path, clock: Clock, store: VoteStore
        ) -> None:
            arena = make_arena(families_dir, clock, store)

            with pytest.raises(UnknownItem):
                arena.start('missing')

    class TestResume:
        def test_resumes_the_open_pair(
            self, families_dir: Path, clock: Clock, store: VoteStore
        ) -> None:
            arena = make_arena(families_dir, clock, store)
            view = arena.start('f')

            resumed = arena.resume(view.id)

            assert resumed == view

        def test_resumes_after_a_restart_without_repeating_a_case(
            self, families_dir: Path, clock: Clock, store: VoteStore
        ) -> None:
            arena = make_arena(families_dir, clock, store)
            first = arena.start('f')
            assert first.pair is not None
            arena.vote(first.pair.token, 'a', Defects(), None)
            restarted = Arena(store, load_families(), RENDER_ORIGIN, clock)

            resumed = restarted.resume(first.id)

            assert (resumed.votes, resumed.remaining) == (1, len(ARENA_CASES) - 1)
            assert resumed.pair is not None
            assert resumed.pair.instruction != first.pair.instruction

        def test_refuses_a_session_it_never_started(
            self, families_dir: Path, clock: Clock, store: VoteStore
        ) -> None:
            arena = make_arena(families_dir, clock, store)

            with pytest.raises(UnknownItem):
                arena.resume('missing')

    class TestVote:
        def test_records_a_vote_with_its_sides_and_duration(
            self, families_dir: Path, clock: Clock, store: VoteStore
        ) -> None:
            arena = make_arena(families_dir, clock, store)
            first = arena.start('f')
            assert first.pair is not None
            clock.now += timedelta(seconds=42)

            second = arena.vote(
                first.pair.token, 'b', Defects(a=frozenset({'tone'})), 'Stiff.'
            )

            (vote,) = store.votes('f')
            assert isinstance(first.pair.a, TextPane)
            assert vote.a.system in first.pair.a.text
            assert (vote.choice, vote.defects, vote.note) == (
                'b',
                Defects(a=frozenset({'tone'})),
                'Stiff.',
            )
            assert vote.duration_sec == 42
            assert second.votes == 1
            assert second.pair is not None
            assert second.pair.token != first.pair.token

        def test_refuses_a_defect_the_family_does_not_list(
            self, families_dir: Path, clock: Clock, store: VoteStore
        ) -> None:
            arena = task_arena(families_dir, clock, store)
            first = arena.start('f')
            assert first.pair is not None

            with pytest.raises(UnlistedDefects, match='tone'):
                arena.vote(
                    first.pair.token,
                    'a',
                    Defects(a=frozenset({'scope'}), b=frozenset({'tone'})),
                    None,
                )

            assert store.votes('f') == []
            assert arena.resume(first.id).pair == first.pair
            arena.vote(first.pair.token, 'a', Defects(a=frozenset({'scope'})), None)
            (vote,) = store.votes('f')
            assert vote.defects == Defects(a=frozenset({'scope'}))

        def test_keeps_the_pair_and_documents_until_its_vote_is_recorded(
            self,
            families_dir: Path,
            clock: Clock,
            store: VoteStore,
            monkeypatch: pytest.MonkeyPatch,
        ) -> None:
            arena = make_arena(
                families_dir,
                clock,
                store,
                texts={'large': '<p>L</p>', 'small': '<p>S</p>'},
                render='html',
            )
            view = arena.start('f')
            assert view.pair is not None
            contents = {
                document: arena.document(document) for document in documents(view.pair)
            }
            defects = Defects(a=frozenset({'tone'}))

            def locked(ballot: Ballot, at: datetime) -> Vote:
                raise sqlite3.OperationalError('database is locked')

            with monkeypatch.context() as patch:
                patch.setattr(store, 'record', locked)

                with pytest.raises(
                    sqlite3.OperationalError, match='database is locked'
                ):
                    arena.vote(view.pair.token, 'b', defects, 'Stiff.')

            assert store.votes('f') == []
            assert arena.resume(view.id) == view
            assert {
                document: arena.document(document) for document in contents
            } == contents

            second = arena.vote(view.pair.token, 'b', defects, 'Stiff.')

            (vote,) = store.votes('f')
            assert (vote.choice, vote.defects, vote.note) == ('b', defects, 'Stiff.')
            assert second.votes == 1
            assert second.pair is not None
            assert second.pair.token != view.pair.token

            with pytest.raises(UnknownItem):
                arena.vote(view.pair.token, 'b', defects, 'Stiff.')

            for document in contents:
                with pytest.raises(UnknownItem):
                    arena.document(document)

        def test_keeps_a_session_open_until_no_case_remains(
            self, families_dir: Path, clock: Clock, store: VoteStore
        ) -> None:
            cases = {f'c{i}': 'reply' for i in range(40)}
            write_votes_family(families_dir, 'f', ARENA_SYSTEMS, cases)
            write_outputs('f', list(passing_outputs(cases, ARENA_SYSTEMS).values()))
            arena = Arena(store, load_families(), RENDER_ORIGIN, clock)
            view = arena.start('f')
            remaining: list[int] = []

            while view.pair is not None:
                remaining.append(view.remaining)
                view = arena.vote(view.pair.token, 'tie', Defects(), None)

            assert remaining == list(range(len(cases), 0, -1))
            assert (view.votes, view.remaining) == (len(cases), 0)

    class TestUndo:
        def test_withdraws_the_vote_and_shows_its_pair_again(
            self, families_dir: Path, clock: Clock, store: VoteStore
        ) -> None:
            arena = make_arena(families_dir, clock, store)
            first = arena.start('f')
            assert first.pair is not None
            arena.vote(first.pair.token, 'a', Defects(), None)

            undone = arena.undo(first.id)

            assert store.votes('f') == []
            assert undone.votes == 0
            assert undone.pair is not None
            assert (undone.pair.a, undone.pair.b) == (first.pair.a, first.pair.b)

        def test_has_nothing_to_undo_once_the_votes_are_withdrawn(
            self, families_dir: Path, clock: Clock, store: VoteStore
        ) -> None:
            arena = make_arena(families_dir, clock, store)
            first = arena.start('f')
            assert first.pair is not None
            arena.vote(first.pair.token, 'a', Defects(), None)
            arena.undo(first.id)

            with pytest.raises(NothingToUndo):
                arena.undo(first.id)

        def test_retires_the_pair_it_replaces(
            self, families_dir: Path, clock: Clock, store: VoteStore
        ) -> None:
            arena = make_arena(families_dir, clock, store)
            first = arena.start('f')
            assert first.pair is not None
            second = arena.vote(first.pair.token, 'a', Defects(), None)
            assert second.pair is not None

            arena.undo(first.id)

            with pytest.raises(UnknownItem):
                arena.vote(second.pair.token, 'a', Defects(), None)

        def test_keeps_a_repeat_a_repeat(
            self,
            families_dir: Path,
            clock: Clock,
            store: VoteStore,
            monkeypatch: pytest.MonkeyPatch,
        ) -> None:
            arena = make_arena(families_dir, clock, store)
            first = arena.start('f')
            assert first.pair is not None
            arena.vote(first.pair.token, 'a', Defects(), None)
            (original,) = store.votes('f')
            clock.now += timedelta(days=1)
            monkeypatch.setattr(scheduler, 'REPEAT_SHARE', 1.0)
            repeat = arena.start('f')
            assert repeat.pair is not None
            arena.vote(repeat.pair.token, 'b', Defects(), None)

            undone = arena.undo(repeat.id)
            assert undone.pair is not None
            arena.vote(undone.pair.token, 'b', Defects(), None)

            *_, recast = store.votes('f')
            assert recast.repeat_of == original.id

        def test_shows_the_pair_again_after_a_restart(
            self, families_dir: Path, clock: Clock, store: VoteStore
        ) -> None:
            arena = make_arena(families_dir, clock, store)
            first = arena.start('f')
            assert first.pair is not None
            arena.vote(first.pair.token, 'a', Defects(), None)
            restarted = Arena(store, load_families(), RENDER_ORIGIN, clock)
            restarted.resume(first.id)

            undone = restarted.undo(first.id)

            assert store.votes('f') == []
            assert undone.pair is not None
            assert (undone.pair.a, undone.pair.b) == (first.pair.a, first.pair.b)

        @pytest.mark.parametrize(
            ('regrade', 'returns'),
            [
                pytest.param({'reward': 1.0, 'gate_failures': []}, True, id='passes'),
                pytest.param(
                    {'reward': 0.0, 'gate_failures': [Gate.TESTS]}, False, id='fails'
                ),
            ],
        )
        def test_keeps_a_vote_on_regraded_solutions_while_both_pass(
            self,
            families_dir: Path,
            clock: Clock,
            store: VoteStore,
            regrade: dict[str, Any],
            returns: bool,
        ) -> None:
            arena = task_arena(families_dir, clock, store)
            first = arena.start('f')
            assert first.pair is not None
            arena.vote(first.pair.token, 'a', Defects(), None)
            (vote,) = store.votes('f')
            write_outputs(
                'f',
                [
                    o.model_copy(update=regrade) if Side.of(o) == vote.a else o
                    for o in read_outputs('f')
                ],
            )
            restarted = Arena(store, load_families(), RENDER_ORIGIN, clock)

            undone = restarted.undo(first.id)

            assert undone.pair is not None
            shown = (undone.pair.a, undone.pair.b)
            assert (shown == (first.pair.a, first.pair.b)) is returns
            assert first.pair.a not in shown or returns

        def test_draws_afresh_once_the_vote_s_output_left(
            self, families_dir: Path, clock: Clock, store: VoteStore
        ) -> None:
            arena = make_arena(families_dir, clock, store)
            first = arena.start('f')
            assert first.pair is not None
            arena.vote(first.pair.token, 'a', Defects(), None)
            (vote,) = store.votes('f')
            write_outputs('f', [o for o in read_outputs('f') if Side.of(o) != vote.a])
            restarted = Arena(store, load_families(), RENDER_ORIGIN, clock)

            undone = restarted.undo(first.id)

            assert store.votes('f') == []
            assert undone.votes == 0
            assert undone.pair is not None
            assert undone.pair.token != first.pair.token

    class TestDocument:
        @pytest.fixture
        def arena(self, families_dir: Path, clock: Clock, store: VoteStore) -> Arena:
            return make_arena(
                families_dir,
                clock,
                store,
                texts={'large': '<p>L</p>', 'small': '<p>S</p>'},
                render='html',
            )

        def test_serves_html_outputs_from_the_render_origin(self, arena: Arena) -> None:
            view = arena.start('f')
            assert view.pair is not None

            served = {arena.document(document) for document in documents(view.pair)}

            assert served == {'<p>L</p>', '<p>S</p>'}

        def test_retires_the_documents_with_their_pair(self, arena: Arena) -> None:
            view = arena.start('f')
            assert view.pair is not None
            tokens = documents(view.pair)
            assert len(tokens) == 2

            arena.vote(view.pair.token, 'a', Defects(), None)

            for document in tokens:
                with pytest.raises(UnknownItem):
                    arena.document(document)
