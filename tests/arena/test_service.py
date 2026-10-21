from datetime import timedelta
from pathlib import Path

import pytest

from assistant_bench.arena import service
from assistant_bench.arena.service import (
    HtmlPane,
    NothingToUndo,
    TextPane,
    UnknownItem,
    load_families,
)
from assistant_bench.arena.store import VoteStore
from assistant_bench.outputs import write_outputs
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
            'scorer = "tests"\nsamples = 1\n'
        )
        (families_dir / 'tests' / 'tasks').mkdir()
        write_outputs(
            'f', [make_output(case='c1', system='large', text='Hi.', gate_failures=[])]
        )

        families = load_families()

        assert list(families) == ['f']
        assert families['f'].cases['c1'].category == 'reply'


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
        ('cut_off', 'choices'),
        [
            (('small',), {'a', 'b'}),
            (('large', 'small'), {'both_bad'}),
        ],
    )
    def test_resolves_pairs_that_fail_the_gates(
        self,
        families_dir: Path,
        clock: Clock,
        store: VoteStore,
        cut_off: tuple[str, ...],
        choices: set[str],
    ) -> None:
        arena = make_arena(families_dir, clock, store, cut_off=cut_off)

        view = arena.start('f')

        assert view.pair is None
        votes = store.votes('f')
        assert len(votes) == len(ARENA_CASES)
        assert {vote.choice for vote in votes} == choices
        assert {vote.auto for vote in votes} == {'gates'}
        for vote in votes:
            winner = {'a': vote.a, 'b': vote.b}.get(vote.choice)
            assert winner is None or winner.system == 'large'

    def test_records_identical_outputs_as_a_tie(
        self, families_dir: Path, clock: Clock, store: VoteStore
    ) -> None:
        arena = make_arena(
            families_dir, clock, store, texts=dict.fromkeys(ARENA_SYSTEMS, 'Same.')
        )

        view = arena.start('f')

        assert view.pair is None
        assert (view.votes, view.size) == (0, 0)
        assert {(vote.choice, vote.auto) for vote in store.votes('f')} == {
            ('tie', 'identical')
        }

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
