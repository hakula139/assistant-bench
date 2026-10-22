from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from assistant_bench.arena import scheduler, service
from assistant_bench.arena.service import (
    Arena,
    HtmlPane,
    NothingToUndo,
    TextPane,
    UnknownItem,
    load_families,
)
from assistant_bench.arena.store import Side, VoteStore
from assistant_bench.outputs import Output, read_outputs, write_outputs
from support import (
    ARENA_CASES,
    ARENA_SYSTEMS,
    RENDER_ORIGIN,
    Clock,
    make_arena,
    make_output,
    write_votes_family,
)


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

    def test_shows_the_instruction_the_trials_ran(
        self, families_dir: Path, data_dir: Path
    ) -> None:
        write_votes_family(families_dir, 'f', ARENA_SYSTEMS, ARENA_CASES)
        (data_dir / 'tasks' / 'f' / 'c1' / 'instruction.md').write_text('As rendered.')
        write_outputs(
            'f', [make_output(case='c1', system='large', text='Hi.', gate_failures=[])]
        )

        families = load_families()

        assert families['f'].instructions == {'c1': 'As rendered.'}


class TestArena:
    def test_shows_a_pair_that_names_no_system(
        self, families_dir: Path, clock: Clock, store: VoteStore
    ) -> None:
        arena = make_arena(families_dir, clock, store)

        view = arena.start('f')

        assert view.pair is not None
        assert (view.votes, view.size) == (0, 3)
        assert view.pair.instruction.startswith('Write about c')
        exposed = view.model_dump_json()
        for hidden in ('fp-', 'job-1', '__', 'large"', 'small"'):
            assert hidden not in exposed

    def test_records_a_vote_with_its_sides_and_duration(
        self, families_dir: Path, clock: Clock, store: VoteStore
    ) -> None:
        arena = make_arena(families_dir, clock, store)
        first = arena.start('f')
        assert first.pair is not None
        clock.now += timedelta(seconds=42)

        second = arena.vote(first.pair.token, 'b', frozenset({'register'}), 'Stiff.')

        (vote,) = store.votes('f')
        assert isinstance(first.pair.a, TextPane)
        assert vote.a.system in first.pair.a.text
        assert (vote.choice, vote.flags, vote.note) == (
            'b',
            frozenset({'register'}),
            'Stiff.',
        )
        assert vote.duration_sec == 42
        assert second.votes == 1
        assert second.pair is not None
        assert second.pair.token != first.pair.token
        with pytest.raises(UnknownItem):
            arena.vote(first.pair.token, 'a', frozenset(), None)

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

        assert (view.votes, view.size, view.pair) == (0, 0, None)
        assert store.votes('f') == []

    def test_undo_withdraws_the_vote_and_shows_its_pair_again(
        self, families_dir: Path, clock: Clock, store: VoteStore
    ) -> None:
        arena = make_arena(families_dir, clock, store)
        first = arena.start('f')
        assert first.pair is not None
        arena.vote(first.pair.token, 'a', frozenset(), None)

        undone = arena.undo(first.id)

        assert store.votes('f') == []
        assert undone.votes == 0
        assert undone.pair is not None
        assert (undone.pair.a, undone.pair.b) == (first.pair.a, first.pair.b)
        with pytest.raises(NothingToUndo):
            arena.undo(first.id)

    def test_undo_draws_afresh_once_the_vote_s_output_left(
        self, families_dir: Path, clock: Clock, store: VoteStore
    ) -> None:
        arena = make_arena(families_dir, clock, store)
        first = arena.start('f')
        assert first.pair is not None
        arena.vote(first.pair.token, 'a', frozenset(), None)
        (vote,) = store.votes('f')
        write_outputs('f', [o for o in read_outputs('f') if Side.of(o) != vote.a])
        restarted = Arena(store, load_families(), RENDER_ORIGIN, clock)

        undone = restarted.undo(first.id)

        assert store.votes('f') == []
        assert undone.votes == 0
        assert undone.pair is not None
        assert undone.pair.token != first.pair.token

    def test_ends_the_session_at_its_size(
        self,
        families_dir: Path,
        clock: Clock,
        store: VoteStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(service, 'SESSION_VOTES', 2)
        arena = make_arena(families_dir, clock, store)
        view = arena.start('f')

        for _ in range(2):
            assert view.pair is not None
            view = arena.vote(view.pair.token, 'tie', frozenset(), None)

        assert (view.votes, view.size, view.pair) == (2, 2, None)

    def test_pairs_the_systems_with_outputs_until_the_others_join(
        self, families_dir: Path, clock: Clock, store: VoteStore
    ) -> None:
        cases = {f'c{i}': 'reply' for i in range(8)}
        write_votes_family(families_dir, 'f', ['ref', *ARENA_SYSTEMS], cases)

        def outputs(systems: list[str]) -> list[Output]:
            return [
                make_output(
                    trial=f'{case}__{system}',
                    job=f'job-{system}',
                    case=case,
                    system=system,
                    fingerprint=f'fp-{system}',
                    text=f'{system} answers {case}',
                    gate_failures=[],
                )
                for case in cases
                for system in systems
            ]

        write_outputs('f', outputs(ARENA_SYSTEMS))
        arena = Arena(store, load_families(), RENDER_ORIGIN, clock)
        view = arena.start('f')
        while view.pair is not None:
            view = arena.vote(view.pair.token, 'a', frozenset(), None)
        write_outputs('f', outputs(['ref', *ARENA_SYSTEMS]))
        clock.now += timedelta(hours=1)
        arena = Arena(store, load_families(), RENDER_ORIGIN, clock)

        joining = arena.start('f')
        assert joining.pair is not None
        arena.vote(joining.pair.token, 'a', frozenset(), None)

        *earlier, joined = store.votes('f')
        assert len(earlier) == len(cases)
        assert {frozenset({vote.a.system, vote.b.system}) for vote in earlier} == {
            frozenset(ARENA_SYSTEMS)
        }
        assert 'ref' in {joined.a.system, joined.b.system}

    def test_refits_the_board_when_a_session_starts(
        self,
        families_dir: Path,
        clock: Clock,
        store: VoteStore,
        monkeypatch: pytest.MonkeyPatch,
        fits: list[int],
    ) -> None:
        arena = make_arena(families_dir, clock, store)
        seeded = arena.start('f')
        assert seeded.pair is not None
        arena.vote(seeded.pair.token, 'a', frozenset(), None)
        monkeypatch.setattr(scheduler, 'SEED_VOTES_PER_SIDE', 0)

        view = arena.start('f')
        assert view.pair is not None
        arena.vote(view.pair.token, 'a', frozenset(), None)
        arena.start('f')

        assert fits == [1, 2]

    def test_resumes_the_open_pair(
        self, families_dir: Path, clock: Clock, store: VoteStore
    ) -> None:
        arena = make_arena(families_dir, clock, store)
        view = arena.start('f')

        assert arena.resume(view.id) == view
        with pytest.raises(UnknownItem):
            arena.resume('missing')
        with pytest.raises(UnknownItem):
            arena.start('missing')

    def test_serves_html_outputs_from_the_render_origin(
        self, families_dir: Path, clock: Clock, store: VoteStore
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
        urls = [
            pane.url
            for pane in (view.pair.a, view.pair.b)
            if isinstance(pane, HtmlPane)
        ]
        assert len(urls) == 2
        assert all(url.startswith(f'{RENDER_ORIGIN}/outputs/') for url in urls)
        documents = [url.rsplit('/', 1)[1] for url in urls]

        served = {arena.document(document) for document in documents}
        arena.vote(view.pair.token, 'a', frozenset(), None)

        assert served == {'<p>L</p>', '<p>S</p>'}
        with pytest.raises(UnknownItem):
            arena.document(documents[0])
