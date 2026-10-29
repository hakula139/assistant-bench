from datetime import UTC, datetime, timedelta
from pathlib import Path

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
from assistant_bench.families import Choice
from assistant_bench.report import Cutoff, Cutoffs
from assistant_bench.scoring.ranking import Comparison
from support import (
    ARENA_NAMES,
    ARENA_SYSTEMS,
    SQLiteDatabase,
    arena_database,
    make_case,
    make_trial,
    make_votes_family,
    record_vote,
)


NOW = datetime(2026, 10, 29, 12, tzinfo=UTC)
FAMILY = make_votes_family()


def published_store() -> tuple[SQLiteDatabase, publication.Publication]:
    cases = [make_case('first'), make_case('second')]
    published = publication.prepare(
        FAMILY,
        cases,
        [make_trial(case.id, system) for case in cases for system in ARENA_SYSTEMS],
        ARENA_NAMES,
        'production',
    )
    database = arena_database()
    publication.publish(database, published, NOW - timedelta(days=1))
    return database, published


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
    local: tuple[Vote, ...] | None = None,
    reference: str = 'acme-large',
    local_reference: str = 'acme-large',
) -> Snapshot:
    def cut(rows: tuple[Vote, ...], excluded: int, reference: str) -> Cut:
        return Cut(
            cutoff=max((row.id for row in rows), default=0),
            inventory=Inventory(
                id='publication',
                family=FAMILY.name,
                reference=reference,
                systems=ARENA_NAMES,
                defects=('facts',),
                cases=tuple(
                    CaseFacts(key=key, category='docs')
                    for key in dict.fromkeys(row.case_key for row in outputs)
                ),
                outputs=outputs,
            ),
            votes=rows,
            excluded=excluded,
        )

    return Snapshot(
        family=FAMILY.name,
        taken_at=NOW,
        production=cut(production, excluded, reference),
        local=None if local is None else cut(local, 0, local_reference),
    )


class TestReadCut:
    def test_cuts_below_the_votes_an_undo_can_still_change(self) -> None:
        database, published = published_store()
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

    def test_reads_every_counted_vote_across_pages(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(votes, 'PAGE_ROWS', 2)
        database, published = published_store()
        recorded = [
            record_vote(
                database, published, NOW, actor=f'actor-{index}', age=timedelta(hours=1)
            )
            for index in range(5)
        ]

        cut = votes.read_cut(database, FAMILY.name, NOW)

        assert cut is not None
        assert [row.id for row in cut.votes] == recorded

    def test_counts_excluded_votes_apart_from_the_counted_ones(self) -> None:
        database, published = published_store()
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

    def test_reads_the_inventory_without_content_or_ip_tags(self) -> None:
        database, published = published_store()
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
                CaseFacts(key=case.key, category=case.category)
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

    def test_cuts_nothing_while_every_vote_can_be_undone(self) -> None:
        database, published = published_store()
        record_vote(database, published, NOW, actor='recent', age=timedelta(minutes=1))

        cut = votes.read_cut(database, FAMILY.name, NOW)

        assert cut is not None
        assert (cut.cutoff, cut.votes, cut.excluded) == (0, (), 0)

    def test_returns_none_for_a_family_without_an_active_publication(self) -> None:
        assert votes.read_cut(arena_database(), FAMILY.name, NOW) is None


class TestStatus:
    def test_summarizes_each_active_family_or_the_named_one(self) -> None:
        database, published = published_store()
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


def moderated_store() -> tuple[SQLiteDatabase, dict[str, int]]:
    """Record four votes from three browsers on two network tags, one excluded."""
    database, published = published_store()
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
        self, selector: Selector | None, expected: list[str]
    ) -> None:
        database, ids = moderated_store()

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
        monkeypatch: pytest.MonkeyPatch,
        selector: Selector,
        expected: list[str],
    ) -> None:
        database, ids = moderated_store()
        monkeypatch.setattr(votes, 'PAGE_ROWS', 2)

        listed = votes.listed(database, FAMILY.name, selector)

        assert [row.id for row in listed] == [ids[name] for name in expected]

    def test_lists_each_vote_with_its_tag_timing_and_reason(self) -> None:
        database, ids = moderated_store()

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

    def test_selects_only_votes_of_the_named_family(self) -> None:
        database, ids = moderated_store()

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
        self, by: votes.Grouping, expected: list[Group]
    ) -> None:
        database, _ = moderated_store()

        assert votes.groups(database, FAMILY.name, by) == expected

    def test_groups_votes_whose_tag_retention_cleared(self) -> None:
        database, ids = moderated_store()
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
        self, selector: Selector, changed: int, excluded: list[str]
    ) -> None:
        database, ids = moderated_store()

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

    def test_leaves_votes_of_another_family_alone(self) -> None:
        database, ids = moderated_store()

        result = votes.exclude(
            database, 'writing-other', Selector(column='id', values=(ids['x'],)), 'bot'
        )

        assert result == 0
        assert votes.listed(database, FAMILY.name, None)[0].exclusion is None

    def test_refuses_a_blank_reason(self) -> None:
        database, _ = moderated_store()

        with pytest.raises(ValueError, match='Give a reason'):
            votes.exclude(
                database, FAMILY.name, Selector(column='actor_id', values=('x',)), ' '
            )

    def test_takes_the_votes_out_of_the_cut_and_the_counts_until_restored(
        self,
    ) -> None:
        database, _ = moderated_store()
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
        self, selector: Selector, changed: int
    ) -> None:
        database, _ = moderated_store()

        result = votes.restore(database, FAMILY.name, selector)

        assert result == changed
        excluded = [
            row.id
            for row in votes.listed(database, FAMILY.name, None)
            if row.exclusion is not None
        ]
        assert excluded == ([] if changed else [3])

    def test_leaves_votes_of_another_family_alone(self) -> None:
        database, ids = moderated_store()

        result = votes.restore(
            database, 'writing-other', Selector(column='id', values=(ids['y-second'],))
        )

        assert result == 0
        (row,) = votes.listed(
            database, FAMILY.name, Selector(column='id', values=(ids['y-second'],))
        )
        assert row.exclusion == 'spam'


class TestSnapshotFiles:
    def test_writes_under_the_family_and_reads_back(self, data_dir: Path) -> None:
        a, b = fact('a', 'c', 'acme-large'), fact('b', 'c', 'acme-small')
        saved = snapshot((a, b), (vote(1, a, b),))

        path = votes.write_snapshot(saved)

        assert path == data_dir / 'votes' / FAMILY.name / '2026-10-29T12-00-00Z.json'
        assert votes.read_snapshot(path) == saved


class TestVotingInput:
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
            snapshot((a, b, failing, twin), rows, excluded=2), FAMILY, []
        )

        assert [row.receipt for row in analysis.ordinary] == ['production:1']
        assert analysis.dropped == 5
        assert analysis.cutoffs == Cutoffs(production=Cutoff(id=4, votes=4), local=None)

    def test_drops_a_vote_on_a_case_whose_text_changed(self) -> None:
        a, b = fact('a', 'c', 'acme-large'), fact('b', 'c', 'acme-small')
        stale = vote(1, a, b).model_copy(update={'case_key': 'edited'})

        analysis = votes.voting_input(snapshot((a, b), (stale,)), FAMILY, [])

        assert (analysis.ordinary, analysis.dropped) == ((), 1)

    def test_records_cutoff_0_for_a_store_without_a_vote(self) -> None:
        a, b = fact('a', 'c', 'acme-large'), fact('b', 'c', 'acme-small')

        analysis = votes.voting_input(snapshot((a, b)), FAMILY, [])

        assert analysis.cutoffs == Cutoffs(production=Cutoff(id=0, votes=0), local=None)

    def test_anchors_on_the_reference_the_publication_froze(self) -> None:
        a, b = fact('a', 'c', 'acme-large'), fact('b', 'c', 'acme-small')

        analysis = votes.voting_input(
            snapshot((a, b), reference='acme-small'), FAMILY, []
        )

        assert analysis.reference == 'acme-small'

    def test_refuses_cuts_that_froze_different_references(self) -> None:
        a, b = fact('a', 'c', 'acme-large'), fact('b', 'c', 'acme-small')
        both = snapshot((a, b), local=(), local_reference='acme-small')

        with pytest.raises(ValueError, match='no single reference'):
            votes.voting_input(both, FAMILY, [])

    def test_prefixes_actors_and_receipts_with_their_store(self) -> None:
        a, b = fact('a', 'c', 'acme-large'), fact('b', 'c', 'acme-small')

        analysis = votes.voting_input(
            snapshot((a, b), (vote(1, a, b),), local=(vote(1, a, b, choice='b'),)),
            FAMILY,
            [],
        )

        assert [(row.receipt, row.actor) for row in analysis.ordinary] == [
            ('production:1', 'production:actor'),
            ('local:1', 'local:actor'),
        ]
        assert analysis.cutoffs == Cutoffs(
            production=Cutoff(id=1, votes=1), local=Cutoff(id=1, votes=1)
        )

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

        analysis = votes.voting_input(snapshot(outputs), FAMILY, [])

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

        analysis = votes.voting_input(snapshot((measured, missing)), FAMILY, [trial])

        by_system = {row.system: row for row in analysis.outputs}
        assert (
            by_system['acme-large'].cost_usd,
            by_system['acme-large'].latency_sec,
        ) == (
            0.5,
            3.0,
        )
        assert by_system['acme-small'].cost_usd is None

    def test_refuses_a_snapshot_of_another_family(self) -> None:
        other = make_votes_family('writing-other')

        with pytest.raises(ValueError, match='belongs to family `writing-fixture`'):
            votes.voting_input(snapshot(()), other, [])
