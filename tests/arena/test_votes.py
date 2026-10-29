from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from assistant_bench.arena import publication, votes
from assistant_bench.arena.publication import CaseFacts, Inventory, OutputFacts
from assistant_bench.arena.votes import Cut, Snapshot, TaggedDefects, Vote
from assistant_bench.families import Choice
from assistant_bench.report import Cutoffs
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
        assert analysis.cutoffs == Cutoffs(production=4, local=None)

    def test_drops_a_vote_on_a_case_whose_text_changed(self) -> None:
        a, b = fact('a', 'c', 'acme-large'), fact('b', 'c', 'acme-small')
        stale = vote(1, a, b).model_copy(update={'case_key': 'edited'})

        analysis = votes.voting_input(snapshot((a, b), (stale,)), FAMILY, [])

        assert (analysis.ordinary, analysis.dropped) == ((), 1)

    def test_records_cutoff_0_for_a_store_without_a_vote(self) -> None:
        a, b = fact('a', 'c', 'acme-large'), fact('b', 'c', 'acme-small')

        analysis = votes.voting_input(snapshot((a, b)), FAMILY, [])

        assert analysis.cutoffs == Cutoffs(production=0, local=None)

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
        assert analysis.cutoffs == Cutoffs(production=1, local=1)

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
