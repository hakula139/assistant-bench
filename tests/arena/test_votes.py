import json
import math
import re
import sqlite3
from collections.abc import Sequence
from contextlib import closing
from datetime import UTC, datetime, timedelta

import pytest

from assistant_bench.arena import publication, votes
from assistant_bench.arena.publication import CaseFacts, Inventory, OutputFacts
from assistant_bench.arena.votes import (
    Cut,
    Group,
    ListedVote,
    Selector,
    Snapshot,
    TaggedDefects,
    Vote,
)
from assistant_bench.database import Param, Result, SQLiteDatabase
from assistant_bench.families import Choice
from assistant_bench.messages import InputError
from assistant_bench.outputs import read_outputs, write_outputs
from assistant_bench.paths import REPO_ROOT
from assistant_bench.report import Cutoff, Cutoffs
from assistant_bench.scoring.ranking import Comparison
from assistant_bench.state import State
from support import (
    ARENA_NAMES,
    ARENA_SYSTEMS,
    MIGRATIONS_DIR,
    make_case,
    make_trial,
    make_votes_family,
    record_vote,
)


NOW = datetime(2026, 10, 29, 12, tzinfo=UTC)
FAMILY = make_votes_family()


def stage_and_activate(
    database: SQLiteDatabase,
    published: publication.Publication,
    now: datetime,
) -> bool:
    publication.stage(database, published, now)
    return publication.activate(database, published.id, now)


def check_pair_counts(database: SQLiteDatabase) -> None:
    rows = database.execute(
        'SELECT family, low_system, high_system, votes FROM pair_vote_counts '
        'WHERE votes > 0 ORDER BY family, low_system, high_system'
    ).rows
    expected = database.execute(
        'SELECT family, min(a_system, b_system) AS low_system, '
        'max(a_system, b_system) AS high_system, count(*) AS votes '
        'FROM votes WHERE exclusion IS NULL GROUP BY family, low_system, high_system '
        'ORDER BY family, low_system, high_system'
    ).rows
    assert rows == expected


@pytest.fixture
def published_store(
    arena_store: SQLiteDatabase,
) -> tuple[SQLiteDatabase, publication.Publication]:
    cases = [make_case('first'), make_case('second')]
    published = publication.prepare(
        FAMILY,
        cases,
        [make_trial(case.id, system) for case in cases for system in ARENA_SYSTEMS],
        ARENA_NAMES,
        target='production',
    )
    stage_and_activate(arena_store, published, NOW - timedelta(days=1))
    return arena_store, published


def fact(
    key: str,
    case: str,
    system: str,
    verdict: str = 'pass',
    content: str | None = None,
) -> OutputFacts:
    return OutputFacts.model_validate(
        {
            'key': key,
            'case_key': case,
            'system': system,
            'fingerprint': f'fp-{system}',
            'verdict': verdict,
            'errored': False,
            'length': 10,
            'content_digest': content or key,
        }
    )


def vote(
    identifier: int,
    a: OutputFacts,
    b: OutputFacts,
    *,
    actor: str = 'actor',
    choice: Choice = 'a',
) -> Vote:
    return Vote(
        id=identifier,
        actor=actor,
        case_key=a.case_key,
        a_output=a.key,
        b_output=b.key,
        a_system=a.system,
        b_system=b.system,
        choice=choice,
        defects=TaggedDefects(b=('facts',)),
        elapsed_ms=4_000,
    )


def snapshot(
    outputs: tuple[OutputFacts, ...],
    production: tuple[Vote, ...] = (),
    *,
    excluded: int = 0,
    reference: str = 'acme-large',
) -> Snapshot:
    return Snapshot(
        family=FAMILY.name,
        taken_at=NOW,
        closed_at=NOW,
        category_order=('docs',),
        closing_revision=None,
        private=None,
        production=Cut(
            cutoff=max((row.id for row in production), default=0),
            inventory=Inventory(
                id='publication',
                family=FAMILY.name,
                reference=reference,
                systems=ARENA_NAMES,
                defects=('facts',),
                cases=tuple(
                    CaseFacts(key=key, category='docs', split='public')
                    for key in dict.fromkeys(row.case_key for row in outputs)
                ),
                outputs=outputs,
            ),
            votes=production,
            excluded=excluded,
        ),
    )


class TestReadCut:
    def test_keeps_inventory_and_exclusions_in_one_read_view(
        self,
        published_store: tuple[SQLiteDatabase, publication.Publication],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        database, published = published_store
        identifier = record_vote(
            database, published, NOW, actor='reader', age=timedelta(hours=1)
        )
        changed = publication.prepare(
            FAMILY,
            [make_case('replacement')],
            [make_trial('replacement', system) for system in ARENA_SYSTEMS],
            ARENA_NAMES,
            target='production',
        )
        publication.stage(database, changed, NOW)
        expected = votes.read_cut(database, FAMILY.name, NOW)
        execute = database.execute
        interposed = False

        def mutate_after_read(sql: str, params: Sequence[Param] = ()) -> Result:
            nonlocal interposed
            result = execute(sql, params)
            if not interposed:
                interposed = True
                execute(
                    'UPDATE active SET publication_id = ? WHERE family = ?',
                    [changed.id, FAMILY.name],
                )
                execute(
                    'UPDATE votes SET exclusion = ? WHERE id = ?', ['spam', identifier]
                )
            return result

        monkeypatch.setattr(database, 'execute', mutate_after_read)

        actual = votes.read_cut(database, FAMILY.name, NOW)

        assert interposed
        assert actual == expected
        latest = votes.read_cut(database, FAMILY.name, NOW)
        assert latest is not None
        assert latest.inventory.id == changed.id
        assert latest.excluded == 1

    def test_cuts_below_the_votes_an_undo_can_still_change(
        self, published_store: tuple[SQLiteDatabase, publication.Publication]
    ) -> None:
        database, published = published_store
        first = record_vote(
            database, published, NOW, actor='first', age=timedelta(hours=1)
        )
        second = record_vote(
            database, published, NOW, actor='second', age=timedelta(minutes=11)
        )
        record_vote(database, published, NOW, actor='recent', age=timedelta(minutes=9))

        cut = votes.read_cut(database, FAMILY.name, NOW)

        assert cut is not None
        assert cut.cutoff == second
        assert [row.id for row in cut.votes] == [first, second]
        assert cut.votes[0].defects == TaggedDefects(b=('facts',))

    def test_cuts_at_the_worker_undo_window(self) -> None:
        limits = (REPO_ROOT / 'web' / 'worker' / 'limits.ts').read_text()
        constants = dict(
            re.findall(r'^(?:export )?const (\w+) = (.+);$', limits, re.MULTILINE)
        )

        def value(expression: str) -> int:
            return math.prod(
                value(constants[factor]) if factor in constants else int(factor)
                for factor in expression.split(' * ')
            )

        assert value(constants['UNDO_MS']) == votes.UNDO_WINDOW_MS

    def test_reads_every_counted_vote_across_pages(
        self,
        published_store: tuple[SQLiteDatabase, publication.Publication],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(votes, 'PAGE_ROWS', 2)
        database, published = published_store
        recorded = [
            record_vote(
                database, published, NOW, actor=f'actor-{index}', age=timedelta(hours=1)
            )
            for index in range(5)
        ]

        cut = votes.read_cut(database, FAMILY.name, NOW)

        assert cut is not None
        assert [row.id for row in cut.votes] == recorded

    def test_counts_excluded_votes_apart_from_the_counted_ones(
        self, published_store: tuple[SQLiteDatabase, publication.Publication]
    ) -> None:
        database, published = published_store
        record_vote(database, published, NOW, actor='kept', age=timedelta(hours=1))
        record_vote(
            database,
            published,
            NOW,
            actor='spam',
            age=timedelta(hours=1),
            exclusion='spam',
        )

        cut = votes.read_cut(database, FAMILY.name, NOW)

        assert cut is not None
        assert [row.actor for row in cut.votes] == ['kept']
        assert cut.excluded == 1

    def test_reads_the_inventory_without_content_or_ip_tags(
        self, published_store: tuple[SQLiteDatabase, publication.Publication]
    ) -> None:
        database, published = published_store
        record_vote(database, published, NOW, actor='voter', age=timedelta(hours=1))

        cut = votes.read_cut(database, FAMILY.name, NOW)

        assert cut is not None
        assert cut.inventory == Inventory(
            id=published.id,
            family=published.family,
            reference=published.reference,
            systems=published.systems,
            defects=published.defects,
            cases=tuple(
                CaseFacts(key=case.key, category=case.category, split=case.split)
                for case in published.cases
            ),
            outputs=tuple(
                OutputFacts.model_validate(output.model_dump(exclude={'content'}))
                for output in published.outputs
            ),
        )
        serialized = cut.model_dump_json()
        assert 'tag-voter' not in serialized
        assert 'on first' not in serialized

    def test_cuts_nothing_while_every_vote_can_be_undone(
        self, published_store: tuple[SQLiteDatabase, publication.Publication]
    ) -> None:
        database, published = published_store
        record_vote(database, published, NOW, actor='recent', age=timedelta(minutes=1))

        cut = votes.read_cut(database, FAMILY.name, NOW)

        assert cut is not None
        assert (cut.cutoff, cut.votes, cut.excluded) == (0, (), 0)

    def test_returns_none_for_a_family_without_an_active_publication(
        self, arena_store: SQLiteDatabase
    ) -> None:
        assert votes.read_cut(arena_store, FAMILY.name, NOW) is None


class TestPairVoteCounts:
    def test_initializes_existing_votes_and_tracks_subsequent_changes(
        self, published_store: tuple[SQLiteDatabase, publication.Publication]
    ) -> None:
        _, published = published_store
        with closing(sqlite3.connect(':memory:', isolation_level=None)) as connection:
            connection.executescript((MIGRATIONS_DIR / '0001_init.sql').read_text())
            connection.executescript(
                (MIGRATIONS_DIR / '0003_case_splits.sql').read_text()
            )
            connection.executescript(
                (MIGRATIONS_DIR / '0004_publication_pair_cases.sql').read_text()
            )
            for path in sorted(MIGRATIONS_DIR.glob('*.sql')):
                if path.name[:4] > '0004':
                    connection.executescript(path.read_text())
            database = SQLiteDatabase(connection)
            stage_and_activate(database, published, NOW)
            first = record_vote(
                database, published, NOW, actor='first', age=timedelta(hours=1)
            )
            second = record_vote(
                database,
                published,
                NOW,
                actor='second',
                age=timedelta(hours=1),
                exclusion='spam',
            )

            connection.executescript(
                (MIGRATIONS_DIR / '0002_pair_vote_counts.sql').read_text()
            )

            check_pair_counts(database)
            for sql, identifier in [
                ('UPDATE votes SET exclusion = NULL WHERE id = ?', second),
                ("UPDATE votes SET exclusion = 'spam' WHERE id = ?", first),
                ('DELETE FROM votes WHERE id = ?', second),
                ('DELETE FROM votes WHERE id = ?', first),
            ]:
                database.execute(sql, [identifier])

                check_pair_counts(database)

            record_vote(database, published, NOW, actor='third', age=timedelta(hours=1))

            check_pair_counts(database)


class TestStatus:
    def test_summarizes_each_active_family_or_the_named_one(
        self, published_store: tuple[SQLiteDatabase, publication.Publication]
    ) -> None:
        database, published = published_store
        record_vote(database, published, NOW, actor='first', age=timedelta(hours=2))
        record_vote(
            database, published, NOW, actor='first', age=timedelta(hours=1), case=1
        )
        record_vote(
            database,
            published,
            NOW,
            actor='spam',
            age=timedelta(minutes=1),
            exclusion='x',
        )

        every = votes.status(database, None)
        named = votes.status(database, FAMILY.name)
        other = votes.status(database, 'writing-other')

        (row,) = every
        assert named == every
        assert other == []
        assert (row.family, row.publication_id) == (FAMILY.name, published.id)
        assert (row.case_count, row.output_count) == (2, 6)
        assert (row.votes, row.excluded, row.voters) == (2, 1, 1)
        assert row.latest_vote_at == int(
            (NOW - timedelta(minutes=1)).timestamp() * 1000
        )


@pytest.fixture
def moderated_store(
    published_store: tuple[SQLiteDatabase, publication.Publication],
) -> tuple[SQLiteDatabase, dict[str, int]]:
    """Record four votes from three browsers on two network tags, one excluded."""
    database, published = published_store
    ids = {
        'x': record_vote(
            database,
            published,
            NOW,
            actor='x',
            age=timedelta(hours=4),
            elapsed_ms=1_000,
            ip_tag='shared',
        ),
        'y-first': record_vote(
            database,
            published,
            NOW,
            actor='y',
            age=timedelta(hours=3),
            choice='b',
            elapsed_ms=3_000,
            ip_tag='shared',
        ),
        'y-second': record_vote(
            database,
            published,
            NOW,
            actor='y',
            age=timedelta(hours=2),
            case=1,
            choice='tie',
            elapsed_ms=8_000,
            ip_tag='mobile',
            exclusion='spam',
        ),
        'z': record_vote(
            database,
            published,
            NOW,
            actor='z',
            age=timedelta(hours=1),
            elapsed_ms=6_000,
            ip_tag='shared',
        ),
    }
    return database, ids


def at(age: timedelta) -> int:
    return int((NOW - age).timestamp() * 1000)


def shares(**values: float) -> dict[Choice, float]:
    return {choice: values.get(choice, 0) for choice in votes.CHOICES}


class TestListed:
    @pytest.mark.parametrize(
        ('selector', 'expected'),
        [
            (None, ['x', 'y-first', 'y-second', 'z']),
            (Selector(column='ip_tag', values=('shared',)), ['x', 'y-first', 'z']),
            (Selector(column='actor_id', values=('y',)), ['y-first', 'y-second']),
            (Selector(column='id', values=(1, 3)), ['x', 'y-second']),
            (Selector(column='actor_id', values=('nobody',)), []),
        ],
        ids=['every', 'ip-tag', 'actor', 'ids', 'no-match'],
    )
    def test_lists_the_selected_votes_in_id_order(
        self,
        moderated_store: tuple[SQLiteDatabase, dict[str, int]],
        selector: Selector | None,
        expected: list[str],
    ) -> None:
        database, ids = moderated_store

        listed = votes.listed(database, FAMILY.name, selector)

        assert [row.id for row in listed] == [ids[name] for name in expected]

    @pytest.mark.parametrize(
        ('selector', 'expected'),
        [
            (Selector(column='ip_tag', values=('shared',)), ['x', 'y-first', 'z']),
            (Selector(column='actor_id', values=('y',)), ['y-first', 'y-second']),
        ],
        ids=['ip-tag', 'actor'],
    )
    def test_pages_through_a_selection_whose_ids_skip(
        self,
        moderated_store: tuple[SQLiteDatabase, dict[str, int]],
        monkeypatch: pytest.MonkeyPatch,
        selector: Selector,
        expected: list[str],
    ) -> None:
        database, ids = moderated_store
        monkeypatch.setattr(votes, 'PAGE_ROWS', 2)

        listed = votes.listed(database, FAMILY.name, selector)

        assert [row.id for row in listed] == [ids[name] for name in expected]

    def test_lists_each_vote_with_its_tag_timing_and_reason(
        self, moderated_store: tuple[SQLiteDatabase, dict[str, int]]
    ) -> None:
        database, ids = moderated_store

        (row,) = votes.listed(
            database, FAMILY.name, Selector(column='id', values=(ids['y-second'],))
        )

        assert row == ListedVote(
            id=ids['y-second'],
            actor='y',
            ip_tag='mobile',
            a_system='acme-large',
            b_system='acme-small',
            choice='tie',
            elapsed_ms=8_000,
            recorded_at=at(timedelta(hours=2)),
            exclusion='spam',
        )

    def test_selects_only_votes_of_the_named_family(
        self, moderated_store: tuple[SQLiteDatabase, dict[str, int]]
    ) -> None:
        database, ids = moderated_store

        listed = votes.listed(
            database, 'writing-other', Selector(column='id', values=(ids['x'],))
        )

        assert listed == []


class TestGroups:
    @pytest.mark.parametrize(
        ('by', 'expected'),
        [
            (
                'ip',
                [
                    Group(
                        subject='shared',
                        votes=3,
                        peers=3,
                        shares=shares(a=2 / 3, b=1 / 3),
                        median_elapsed_ms=3_000,
                        first_at=at(timedelta(hours=4)),
                        last_at=at(timedelta(hours=1)),
                        excluded=0,
                    ),
                    Group(
                        subject='mobile',
                        votes=1,
                        peers=1,
                        shares=shares(tie=1),
                        median_elapsed_ms=8_000,
                        first_at=at(timedelta(hours=2)),
                        last_at=at(timedelta(hours=2)),
                        excluded=1,
                    ),
                ],
            ),
            (
                'actor',
                [
                    Group(
                        subject='y',
                        votes=2,
                        peers=2,
                        shares=shares(b=0.5, tie=0.5),
                        median_elapsed_ms=5_500,
                        first_at=at(timedelta(hours=3)),
                        last_at=at(timedelta(hours=2)),
                        excluded=1,
                    ),
                    Group(
                        subject='x',
                        votes=1,
                        peers=1,
                        shares=shares(a=1),
                        median_elapsed_ms=1_000,
                        first_at=at(timedelta(hours=4)),
                        last_at=at(timedelta(hours=4)),
                        excluded=0,
                    ),
                    Group(
                        subject='z',
                        votes=1,
                        peers=1,
                        shares=shares(a=1),
                        median_elapsed_ms=6_000,
                        first_at=at(timedelta(hours=1)),
                        last_at=at(timedelta(hours=1)),
                        excluded=0,
                    ),
                ],
            ),
        ],
    )
    def test_summarizes_each_subject_most_votes_first(
        self,
        moderated_store: tuple[SQLiteDatabase, dict[str, int]],
        by: votes.Grouping,
        expected: list[Group],
    ) -> None:
        database, _ = moderated_store

        assert votes.groups(database, FAMILY.name, by) == expected

    def test_groups_votes_whose_tag_retention_cleared(
        self, moderated_store: tuple[SQLiteDatabase, dict[str, int]]
    ) -> None:
        database, ids = moderated_store
        database.execute(
            'UPDATE votes SET ip_tag = NULL WHERE id IN (?, ?)',
            [ids['y-first'], ids['y-second']],
        )

        by_ip = votes.groups(database, FAMILY.name, 'ip')
        by_actor = votes.groups(database, FAMILY.name, 'actor')

        assert [(group.subject, group.votes) for group in by_ip] == [
            ('shared', 2),
            (None, 2),
        ]
        assert [(group.subject, group.peers) for group in by_actor] == [
            ('y', 0),
            ('x', 1),
            ('z', 1),
        ]


class TestExclude:
    @pytest.mark.parametrize(
        ('selector', 'changed', 'excluded'),
        [
            (Selector(column='ip_tag', values=('shared',)), 3, ['x', 'y-first', 'z']),
            (Selector(column='actor_id', values=('y',)), 1, ['y-first']),
            (Selector(column='id', values=(1, 4)), 2, ['x', 'z']),
            (Selector(column='id', values=(*range(1_000, 1_150), 1)), 1, ['x']),
            (Selector(column='ip_tag', values=('unknown',)), 0, []),
        ],
        ids=['ip-tag', 'actor', 'ids', 'ids-past-the-parameter-limit', 'no-match'],
    )
    def test_excludes_the_selected_votes_keeping_earlier_reasons(
        self,
        moderated_store: tuple[SQLiteDatabase, dict[str, int]],
        selector: Selector,
        changed: int,
        excluded: list[str],
    ) -> None:
        database, ids = moderated_store

        result = votes.exclude(database, FAMILY.name, selector, 'bot')

        assert result == changed
        reasons = {
            row.id: row.exclusion for row in votes.listed(database, FAMILY.name, None)
        }
        assert reasons == {
            ids[name]: 'spam'
            if name == 'y-second'
            else 'bot'
            if name in excluded
            else None
            for name in ids
        }
        check_pair_counts(database)
        assert votes.exclude(database, FAMILY.name, selector, 'bot') == 0
        check_pair_counts(database)

    def test_leaves_votes_of_another_family_alone(
        self, moderated_store: tuple[SQLiteDatabase, dict[str, int]]
    ) -> None:
        database, ids = moderated_store

        result = votes.exclude(
            database, 'writing-other', Selector(column='id', values=(ids['x'],)), 'bot'
        )

        assert result == 0
        assert votes.listed(database, FAMILY.name, None)[0].exclusion is None

    def test_refuses_a_blank_reason(
        self, moderated_store: tuple[SQLiteDatabase, dict[str, int]]
    ) -> None:
        database, _ = moderated_store

        with pytest.raises(ValueError, match='Give a reason'):
            votes.exclude(
                database, FAMILY.name, Selector(column='actor_id', values=('x',)), ' '
            )

    def test_takes_the_votes_out_of_the_cut_and_the_counts_until_restored(
        self, moderated_store: tuple[SQLiteDatabase, dict[str, int]]
    ) -> None:
        database, _ = moderated_store
        shared = Selector(column='ip_tag', values=('shared',))

        votes.exclude(database, FAMILY.name, shared, 'bot')
        excluded_cut = votes.read_cut(database, FAMILY.name, NOW)
        (excluded_status,) = votes.status(database, FAMILY.name)
        votes.restore(database, FAMILY.name, shared)
        restored_cut = votes.read_cut(database, FAMILY.name, NOW)
        (restored_status,) = votes.status(database, FAMILY.name)

        assert excluded_cut is not None and restored_cut is not None
        assert (excluded_cut.votes, excluded_cut.excluded) == ((), 4)
        assert [row.actor for row in restored_cut.votes] == ['x', 'y', 'z']
        assert restored_cut.excluded == 1
        assert (excluded_status.votes, excluded_status.voters) == (0, 0)
        assert (restored_status.votes, restored_status.voters) == (3, 3)


class TestRestore:
    @pytest.mark.parametrize(
        ('selector', 'changed'),
        [
            (Selector(column='ip_tag', values=('mobile',)), 1),
            (Selector(column='actor_id', values=('y',)), 1),
            (Selector(column='id', values=(3,)), 1),
            (Selector(column='actor_id', values=('x',)), 0),
        ],
        ids=['ip-tag', 'actor', 'ids', 'counted'],
    )
    def test_counts_the_selected_excluded_votes_again(
        self,
        moderated_store: tuple[SQLiteDatabase, dict[str, int]],
        selector: Selector,
        changed: int,
    ) -> None:
        database, _ = moderated_store

        result = votes.restore(database, FAMILY.name, selector)

        assert result == changed
        excluded = [
            row.id
            for row in votes.listed(database, FAMILY.name, None)
            if row.exclusion is not None
        ]
        assert excluded == ([] if changed else [3])
        check_pair_counts(database)
        assert votes.restore(database, FAMILY.name, selector) == 0
        check_pair_counts(database)

    def test_leaves_votes_of_another_family_alone(
        self, moderated_store: tuple[SQLiteDatabase, dict[str, int]]
    ) -> None:
        database, ids = moderated_store

        result = votes.restore(
            database, 'writing-other', Selector(column='id', values=(ids['y-second'],))
        )

        assert result == 0
        (row,) = votes.listed(
            database, FAMILY.name, Selector(column='id', values=(ids['y-second'],))
        )
        assert row.exclusion == 'spam'


class TestSnapshots:
    def test_persists_private_vote_facts_without_candidate_bodies(
        self, private_store: SQLiteDatabase
    ) -> None:
        case = make_case('held', 'held-out').model_copy(
            update={'instruction': 'Private instruction marker.'}
        )
        published = publication.prepare(
            FAMILY,
            [case],
            [
                make_trial('held', system, text=f'Private candidate marker {system}.')
                for system in ARENA_SYSTEMS
            ],
            ARENA_NAMES,
            target='private',
        )
        publication.stage(private_store, published, NOW)
        publication.activate(private_store, published.id, NOW)
        record_vote(
            private_store, published, NOW, actor='reader', age=timedelta(hours=1)
        )
        cut = votes.read_cut(private_store, FAMILY.name, NOW)
        saved = Snapshot(
            family=FAMILY.name,
            taken_at=NOW,
            closed_at=NOW,
            category_order=('docs',),
            closing_revision=None,
            production=None,
            private=cut,
        )

        identity = votes.save_snapshot(private_store, saved)
        restored = votes.read_snapshot(private_store, identity)

        assert restored == saved
        assert 'Private instruction marker' not in restored.model_dump_json()
        assert 'Private candidate marker' not in restored.model_dump_json()
        assert len(votes.voting_input(restored, {}).ordinary) == 1

    def test_replays_frozen_facts_after_moderation_and_publication_change(
        self, private_store: SQLiteDatabase
    ) -> None:
        outputs = [
            make_trial('first', system, cost_usd=0.25) for system in ARENA_SYSTEMS
        ]
        published = publication.prepare(
            FAMILY,
            [make_case('first')],
            outputs,
            ARENA_NAMES,
            target='production',
        )
        publication.stage(private_store, published, NOW)
        publication.activate(private_store, published.id, NOW)
        identifier = record_vote(
            private_store, published, NOW, actor='reader', age=timedelta(hours=1)
        )
        cut = votes.read_cut(private_store, FAMILY.name, NOW)
        assert cut is not None
        state = State(private_store)
        generation = write_outputs(
            FAMILY.name,
            outputs,
            state,
            previous=None,
            code_revision='a' * 40,
            case_revision='b' * 40,
            system_names=ARENA_NAMES,
        )
        cut = cut.model_copy(update={'output_generation': generation})
        saved = Snapshot(
            family=FAMILY.name,
            taken_at=NOW,
            closed_at=NOW + timedelta(hours=2),
            category_order=('reply', 'docs'),
            closing_revision=None,
            production=cut,
            private=None,
        )

        identity = votes.save_snapshot(private_store, saved)
        private_store.execute(
            'UPDATE votes SET exclusion = ? WHERE id = ?', ['spam', identifier]
        )
        private_store.execute('DELETE FROM active')
        write_outputs(
            FAMILY.name,
            [output.model_copy(update={'cost_usd': 99.0}) for output in outputs],
            state,
            previous=generation,
            code_revision='a' * 40,
            case_revision='c' * 40,
            system_names=ARENA_NAMES,
        )

        restored = votes.read_snapshot(private_store, identity)
        assert restored == saved
        assert votes.save_snapshot(private_store, saved) == identity
        assert restored.production is not None
        assert restored.production.output_generation == generation
        analysis = votes.voting_input(
            restored,
            {
                'production': read_outputs(
                    FAMILY.name,
                    state,
                    generation=state.collection_generation(generation),
                )
            },
        )
        assert analysis.declared_categories == ('reply', 'docs')
        assert len(analysis.ordinary) == 1
        assert {output.cost_usd for output in analysis.outputs} == {0.25}

    def test_keeps_interrupted_rows_unreadable_until_complete_resumption(
        self, private_store: SQLiteDatabase, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        a, b = fact('a', 'c', 'acme-large'), fact('b', 'c', 'acme-small')
        saved = snapshot((a, b), (vote(1, a, b),))
        execute = private_store.execute

        def interrupt(sql: str, params: Sequence[Param] = ()) -> Result:
            if sql.startswith('INSERT INTO vote_snapshot_outputs'):
                raise InputError('Interrupted staging.')
            return execute(sql, params)

        monkeypatch.setattr(private_store, 'execute', interrupt)

        with pytest.raises(InputError, match='Interrupted staging'):
            votes.save_snapshot(private_store, saved)

        identity = str(execute('SELECT id FROM vote_snapshots').rows[0]['id'])
        with pytest.raises(InputError, match='not sealed'):
            votes.read_snapshot(private_store, identity)
        with pytest.raises(sqlite3.IntegrityError, match='every expected row'):
            execute('UPDATE vote_snapshots SET sealed = 1 WHERE id = ?', [identity])
        monkeypatch.setattr(private_store, 'execute', execute)

        assert votes.save_snapshot(private_store, saved) == identity
        assert votes.read_snapshot(private_store, identity) == saved

    def test_refuses_corrupted_staging_before_sealing(
        self, private_store: SQLiteDatabase, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        a, b = fact('a', 'c', 'acme-large'), fact('b', 'c', 'acme-small')
        saved = snapshot((a, b), (vote(1, a, b),))
        execute = private_store.execute

        def corrupt(sql: str, params: Sequence[Param] = ()) -> Result:
            if sql.startswith('INSERT INTO vote_snapshot_outputs'):
                payload = json.loads(str(params[-1]))
                payload['length'] = 999
                params = [*params[:-1], json.dumps(payload)]
            return execute(sql, params)

        monkeypatch.setattr(private_store, 'execute', corrupt)

        with pytest.raises(InputError, match='incomplete or conflicting facts'):
            votes.save_snapshot(private_store, saved)

        assert execute('SELECT sealed FROM vote_snapshots').rows == [{'sealed': 0}]

    @pytest.mark.parametrize('change', ['update', 'delete', 'insert'])
    def test_sealed_rows_reject_later_changes(
        self, private_store: SQLiteDatabase, change: str
    ) -> None:
        a, b = fact('a', 'c', 'acme-large'), fact('b', 'c', 'acme-small')
        saved = snapshot((a, b), (vote(1, a, b),))
        identity = votes.save_snapshot(private_store, saved)
        statements = {
            'update': (
                'UPDATE vote_snapshot_outputs SET facts_json = ? WHERE snapshot_id = ?',
                ['{}', identity],
            ),
            'delete': (
                'DELETE FROM vote_snapshot_votes WHERE snapshot_id = ?',
                [identity],
            ),
            'insert': (
                'INSERT INTO vote_snapshot_cases VALUES (?, ?, ?, ?)',
                [identity, 'production', 'extra', '{}'],
            ),
        }
        sql, params = statements[change]

        with pytest.raises(sqlite3.IntegrityError):
            private_store.execute(sql, params)

        assert votes.read_snapshot(private_store, identity) == saved

    @pytest.mark.parametrize('change', ['digest', 'count'])
    def test_read_reverifies_count_and_digest_after_external_corruption(
        self, private_store: SQLiteDatabase, change: str
    ) -> None:
        a, b = fact('a', 'c', 'acme-large'), fact('b', 'c', 'acme-small')
        saved = snapshot((a, b), (vote(1, a, b),))
        identity = votes.save_snapshot(private_store, saved)
        if change == 'digest':
            private_store.execute('DROP TRIGGER snapshot_outputs_keep_content')
            private_store.execute(
                'UPDATE vote_snapshot_outputs SET facts_json = '
                "json_set(facts_json, '$.length', 999)"
            )
        else:
            private_store.execute('DROP TRIGGER snapshots_keep_identity')
            private_store.execute(
                'UPDATE vote_snapshots SET expected_output_count = '
                'expected_output_count + 1'
            )

        with pytest.raises(InputError, match='incomplete or conflicting facts'):
            votes.read_snapshot(private_store, identity)


class TestVotingInput:
    @pytest.mark.parametrize(
        'private_pin', ['generation-b', None], ids=['pinned', 'unknown']
    )
    def test_measures_each_cut_from_its_own_generation(
        self, private_pin: str | None
    ) -> None:
        public = make_trial('public', 'acme-large', cost_usd=0.25, reward=1.0)
        private = make_trial('private', 'acme-large', cost_usd=0.75, reward=0.5)
        public_key = publication.output_key(FAMILY.name, public)
        private_key = publication.output_key(FAMILY.name, private)
        first = snapshot((fact(public_key, 'open', 'acme-large'),)).production
        second = snapshot((fact(private_key, 'held', 'acme-large'),)).production
        assert first is not None and second is not None
        frozen = Snapshot(
            family=FAMILY.name,
            taken_at=NOW,
            closed_at=NOW,
            category_order=('reply', 'docs'),
            closing_revision=None,
            production=first.model_copy(update={'output_generation': 'generation-a'}),
            private=second.model_copy(update={'output_generation': private_pin}),
        )

        analysis = votes.voting_input(
            frozen,
            {
                'production': [public, private.model_copy(update={'cost_usd': 99.0})],
                'private': [private, public.model_copy(update={'cost_usd': 88.0})],
            },
        )

        measured = {row.case: (row.cost_usd, row.reward) for row in analysis.outputs}
        assert measured == {
            'open': (0.25, 1.0),
            'held': (0.75, 0.5) if private_pin is not None else (None, None),
        }
        assert analysis.declared_categories == ('reply', 'docs')

    def test_unions_partly_overlapping_system_fields_in_store_order(self) -> None:
        a, b = fact('a', 'public', 'acme-large'), fact('b', 'public', 'acme-small')
        c, d = fact('c', 'held', 'acme-large'), fact('d', 'held', 'acme-pro')
        production = snapshot((a, b), (vote(1, a, b),)).production
        private = snapshot((c, d), (vote(1, c, d),)).production
        assert production is not None and private is not None
        production = production.model_copy(
            update={
                'inventory': production.inventory.model_copy(
                    update={
                        'systems': {
                            system: ARENA_NAMES[system]
                            for system in ('acme-large', 'acme-small')
                        }
                    }
                )
            }
        )
        private = private.model_copy(
            update={
                'inventory': private.inventory.model_copy(
                    update={
                        'systems': {
                            system: ARENA_NAMES[system]
                            for system in ('acme-large', 'acme-pro')
                        }
                    }
                )
            }
        )
        merged = Snapshot(
            family=FAMILY.name,
            taken_at=NOW,
            closed_at=NOW,
            category_order=('docs',),
            closing_revision=None,
            production=production,
            private=private,
        )

        analysis = votes.voting_input(merged, {})

        assert analysis.systems == ('acme-large', 'acme-small', 'acme-pro')
        assert list(votes.system_names(merged)) == list(analysis.systems)
        assert {row.comparison.right for row in analysis.ordinary} == {
            'acme-small',
            'acme-pro',
        }

    def test_merges_disjoint_stores_with_independent_actors_and_cut_validation(
        self,
    ) -> None:
        a, b = fact('a', 'public', 'acme-large'), fact('b', 'public', 'acme-small')
        c, d = fact('c', 'held', 'acme-large'), fact('d', 'held', 'acme-small')
        production = snapshot(
            (a, b), tuple(vote(index, a, b) for index in range(1, 102))
        )
        private = snapshot((c, d), (vote(1, c, d), vote(2, a, b))).production
        assert private is not None
        private = private.model_copy(
            update={
                'inventory': private.inventory.model_copy(
                    update={
                        'cases': (
                            CaseFacts(key='held', category='docs', split='held-out'),
                        ),
                    }
                )
            }
        )
        merged = Snapshot(
            family=FAMILY.name,
            taken_at=NOW,
            closed_at=NOW,
            category_order=('docs',),
            closing_revision=None,
            production=production.production,
            private=private,
        )

        analysis = votes.voting_input(merged, {})

        assert {row.actor for row in analysis.ordinary} == {
            'production:actor',
            'private:actor',
        }
        assert sum(row.comparison.weight for row in analysis.ordinary) == pytest.approx(
            101
        )
        assert analysis.dropped == 1
        assert analysis.cutoffs == Cutoffs(
            production=Cutoff(id=101, votes=101), private=Cutoff(id=2, votes=2)
        )
        assert dict(analysis.splits) == {'public': 'public', 'held': 'held-out'}

    @pytest.mark.parametrize(
        'mismatch',
        [
            'cases',
            'outputs',
            'family',
            'reference',
            'systems',
            'defects',
            'fingerprint',
            'cutoff',
        ],
    )
    def test_rejects_incompatible_store_cuts(self, mismatch: str) -> None:
        a, b = fact('a', 'public', 'acme-large'), fact('b', 'public', 'acme-small')
        c, d = fact('c', 'held', 'acme-large'), fact('d', 'held', 'acme-small')
        production = snapshot((a, b), (vote(1, a, b),)).production
        private = snapshot((c, d), (vote(1, c, d),)).production
        assert production is not None and private is not None
        variants: dict[str, dict[str, object]] = {
            'cases': {'cases': production.inventory.cases},
            'outputs': {'outputs': (a, d)},
            'family': {'family': 'other'},
            'reference': {'reference': 'acme-small'},
            'systems': {'systems': ARENA_NAMES | {'acme-large': 'Different label'}},
            'defects': {'defects': ()},
            'fingerprint': {
                'outputs': (c.model_copy(update={'fingerprint': 'changed'}), d)
            },
        }
        changes = variants.get(mismatch, {})
        private = private.model_copy(
            update={'cutoff': 0}
            if mismatch == 'cutoff'
            else {'inventory': private.inventory.model_copy(update=changes)}
        )

        with pytest.raises(ValueError):
            Snapshot(
                family=FAMILY.name,
                taken_at=NOW,
                closed_at=NOW,
                category_order=('docs',),
                closing_revision=None,
                production=production,
                private=private,
            )

    def test_counts_votes_both_of_whose_outputs_still_pass_distinctly(self) -> None:
        a, b = fact('a', 'c', 'acme-large'), fact('b', 'c', 'acme-small')
        failing = fact('f', 'c', 'acme-pro', 'fail')
        twin = fact('t', 'c', 'acme-pro', content='a')
        retired = fact('r', 'c', 'acme-pro')
        rows = (
            vote(1, a, b),
            vote(2, a, failing, actor='other'),
            vote(3, a, twin, actor='third'),
            vote(4, a, retired, actor='fourth'),
        )

        analysis = votes.voting_input(
            snapshot((a, b, failing, twin), rows, excluded=2), {}
        )

        assert [row.actor for row in analysis.ordinary] == ['production:actor']
        assert analysis.dropped == 5
        assert analysis.cutoffs == Cutoffs(
            production=Cutoff(id=4, votes=4), private=None
        )

    def test_drops_a_vote_on_a_case_whose_text_changed(self) -> None:
        a, b = fact('a', 'c', 'acme-large'), fact('b', 'c', 'acme-small')
        stale = vote(1, a, b).model_copy(update={'case_key': 'edited'})

        analysis = votes.voting_input(snapshot((a, b), (stale,)), {})

        assert (analysis.ordinary, analysis.dropped) == ((), 1)

    def test_records_cutoff_0_for_a_store_without_a_vote(self) -> None:
        a, b = fact('a', 'c', 'acme-large'), fact('b', 'c', 'acme-small')

        analysis = votes.voting_input(snapshot((a, b)), {})

        assert analysis.cutoffs == Cutoffs(
            production=Cutoff(id=0, votes=0), private=None
        )

    def test_anchors_on_the_reference_the_publication_froze(self) -> None:
        a, b = fact('a', 'c', 'acme-large'), fact('b', 'c', 'acme-small')

        analysis = votes.voting_input(snapshot((a, b), reference='acme-small'), {})

        assert analysis.reference == 'acme-small'

    def test_settles_the_pairings_verdicts_and_identical_content_decide(
        self,
    ) -> None:
        outputs = (
            fact('a1', 'won', 'acme-large'),
            fact('a2', 'won', 'acme-large'),
            fact('b1', 'won', 'acme-small', 'fail'),
            fact('b2', 'won', 'acme-small', 'fail'),
            fact('a3', 'failed', 'acme-large', 'fail'),
            fact('b3', 'failed', 'acme-small', 'fail'),
            fact('a4', 'same', 'acme-large', content='same'),
            fact('b4', 'same', 'acme-small', content='same'),
            fact('c4', 'same', 'acme-pro', 'ungraded'),
            fact('a5', 'voted', 'acme-large'),
            fact('b5', 'voted', 'acme-small'),
        )

        analysis = votes.voting_input(snapshot(outputs), {})

        assert sorted(analysis.settlements) == sorted(
            [
                *[Comparison('acme-large', 'acme-small', 1.0, 'won', 0.25, False)] * 4,
                Comparison('acme-large', 'acme-small', 0.5, 'failed', 1.0, False),
                Comparison('acme-large', 'acme-small', 0.5, 'same', 1.0, False),
            ]
        )

    def test_measures_outputs_from_the_collected_trials_by_key(self) -> None:
        trial = make_trial('c', 'acme-large', cost_usd=0.5, latency_sec=3.0)
        key = publication.output_key(FAMILY.name, trial)
        measured, missing = (
            fact(key, 'c', 'acme-large'),
            fact('gone', 'c', 'acme-small'),
        )

        saved = snapshot((measured, missing))
        assert saved.production is not None
        saved = saved.model_copy(
            update={
                'production': saved.production.model_copy(
                    update={'output_generation': 'original'}
                )
            }
        )

        analysis = votes.voting_input(saved, {'production': [trial]})

        by_system = {row.system: row for row in analysis.outputs}
        assert (
            by_system['acme-large'].cost_usd,
            by_system['acme-large'].latency_sec,
        ) == (
            0.5,
            3.0,
        )
        assert by_system['acme-small'].cost_usd is None
