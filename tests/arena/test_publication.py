import json
import sqlite3
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from assistant_bench.arena import publication
from assistant_bench.arena.d1 import Param, Result
from assistant_bench.arena.publication import Publication, VotingTarget
from assistant_bench.families import (
    Case,
    CaseSource,
    Family,
    Gate,
)
from assistant_bench.outputs import Output
from support import (
    ARENA_NAMES,
    ARENA_SYSTEMS,
    CODE_DEFECTS,
    FIXTURES,
    SQLiteDatabase,
    arena_database,
    make_case,
    make_trial,
    make_votes_family,
)


PUBLICATION_FIXTURE = FIXTURES / 'arena-publication.sql'
NOW = datetime(2026, 10, 29, 12, tzinfo=UTC)


def prepare(
    family: Family,
    cases: Sequence[Case],
    outputs: Sequence[Output],
    target: VotingTarget = 'production',
) -> Publication:
    return publication.prepare(family, cases, outputs, ARENA_NAMES, target)


def text_publication() -> Publication:
    cases = [make_case('first'), make_case('second', category='reply')]
    return prepare(
        make_votes_family(),
        cases,
        [
            *(make_trial('first', system) for system in ARENA_SYSTEMS),
            make_trial('second', 'acme-large', text='Same reply.'),
            make_trial('second', 'acme-small', text='Same reply.'),
            make_trial('second', 'acme-pro', [Gate.LENGTH]),
        ],
    )


def diff_publication() -> Publication:
    family = make_votes_family('coding-fixture', diff=True)
    return prepare(
        family,
        [make_case('fix-parser', category='bug-fix')],
        [
            make_trial('fix-parser', 'acme-large', text='--- a/x\n+++ b/x\n+fixed\n'),
            make_trial('fix-parser', 'acme-small', text='--- a/x\n+++ b/x\n+also\n'),
            make_trial(
                'fix-parser', 'acme-pro', [Gate.TESTS], text='--- a/x\n+++ b/x\n'
            ),
        ],
    )


def rows(database: SQLiteDatabase, sql: str) -> list[tuple[Any, ...]]:
    return database.connection.execute(sql).fetchall()


class Interrupted(Exception):
    pass


class RecordingDatabase:
    """Records each statement and delegates it, raising at the first that `fails`."""

    def __init__(
        self, database: SQLiteDatabase, fails: Callable[[str], bool] = lambda _: False
    ):
        self.database = database
        self.fails = fails
        self.statements: list[str] = []

    def execute(self, sql: str, params: Sequence[Param] = ()) -> Result:
        self.statements.append(sql)
        if self.fails(sql):
            raise Interrupted(sql)
        return self.database.execute(sql, params)


class TestVotingTarget:
    @pytest.mark.parametrize(
        ('public_voting', 'source', 'target'),
        [
            (True, 'synthetic', 'production'),
            (True, 'public', 'production'),
            (True, 'transcript', 'local'),
            (True, 'held-out', 'local'),
            (False, 'synthetic', 'local'),
            (False, 'public', 'local'),
        ],
    )
    def test_votes_only_public_cases_of_public_families_in_production(
        self, public_voting: bool, source: CaseSource, target: VotingTarget
    ) -> None:
        family = make_votes_family(public_voting=public_voting)

        assert publication.voting_target(family, make_case('c', source)) == target


class TestPrepare:
    def test_partitions_the_cases_between_production_and_the_local_store(
        self,
    ) -> None:
        family = make_votes_family()
        cases = [
            make_case('open', 'synthetic'),
            make_case('quoted', 'public'),
            make_case('private', 'transcript'),
            make_case('secret', 'held-out'),
        ]
        outputs = [
            make_trial(case.id, system) for case in cases for system in ARENA_SYSTEMS
        ]
        by_key = {publication.case_key(case): case.id for case in cases}

        production = prepare(family, cases, outputs, 'production')
        local = prepare(family, cases, outputs, 'local')

        assert sorted(by_key[case.key] for case in production.cases) == [
            'open',
            'quoted',
        ]
        assert sorted(by_key[case.key] for case in local.cases) == [
            'private',
            'secret',
        ]
        assert {output.case_key for output in production.outputs} == {
            case.key for case in production.cases
        }

    def test_publishes_every_case_of_a_family_without_public_voting_locally(
        self,
    ) -> None:
        family = make_votes_family(public_voting=False)
        cases = [make_case('open'), make_case('private', 'transcript')]
        outputs = [
            make_trial(case.id, system) for case in cases for system in ARENA_SYSTEMS
        ]

        local = prepare(family, cases, outputs, 'local')

        assert len(local.cases) == 2
        with pytest.raises(ValueError, match='sets no `public_voting`'):
            prepare(family, cases, outputs, 'production')

    def test_publishes_task_diffs_with_verdicts_from_the_tests_gate(self) -> None:
        published = diff_publication()

        outputs = {output.system: output for output in published.outputs}
        assert published.render == 'diff'
        assert published.defects == CODE_DEFECTS
        assert outputs['acme-large'].verdict == 'pass'
        assert outputs['acme-large'].content == '--- a/x\n+++ b/x\n+fixed\n'
        assert outputs['acme-pro'].verdict == 'fail'
        assert outputs['acme-pro'].content is None

    def test_keeps_content_only_on_passing_outputs(self) -> None:
        family = make_votes_family()
        cases = [make_case('c')]

        published = prepare(
            family,
            cases,
            [
                make_trial('c', 'acme-large'),
                make_trial('c', 'acme-small'),
                make_trial('c', 'acme-pro', [Gate.EMPTY], text=''),
                make_trial('c', 'unlisted'),
                make_trial(
                    'c', 'acme-pro', gate_failures=None, error='Timeout', trial='c__err'
                ),
            ],
        )

        verdicts = sorted(
            (output.system, output.verdict, output.content is not None)
            for output in published.outputs
        )
        assert verdicts == [
            ('acme-large', 'pass', True),
            ('acme-pro', 'fail', False),
            ('acme-pro', 'ungraded', False),
            ('acme-small', 'pass', True),
        ]
        assert published.systems == ARENA_NAMES

    def test_keeps_the_keys_of_unchanged_cases_and_outputs_across_republication(
        self,
    ) -> None:
        family = make_votes_family()
        first = [make_case('first'), make_case('second')]
        outputs = [
            make_trial(case.id, system) for case in first for system in ARENA_SYSTEMS
        ]
        before = prepare(family, first, outputs)
        edited = [first[0], make_case('second').model_copy(update={'checklist': []})]
        rerun = make_trial('second', 'acme-pro', trial='second__acme-pro__rerun')

        after = prepare(family, edited, [*outputs[:-1], rerun])

        kept = publication.case_key(first[0])
        assert kept in {case.key for case in before.cases}
        assert kept in {case.key for case in after.cases}
        assert publication.case_key(first[1]) not in {case.key for case in after.cases}
        assert {output.key for output in before.outputs if output.case_key == kept} == {
            output.key for output in after.outputs if output.case_key == kept
        }
        assert publication.output_key(family.name, rerun) in {
            output.key for output in after.outputs
        }
        assert after.id != before.id

    @pytest.mark.parametrize(
        ('outputs', 'message'),
        [
            (
                [
                    make_trial('c', 'acme-large', fingerprint=None),
                    make_trial('c', 'acme-small'),
                ],
                'outputs of `acme-large` lack a fingerprint',
            ),
            (
                [
                    make_trial('c', 'acme-large'),
                    make_trial('c', 'acme-small', [Gate.EMPTY]),
                ],
                'no production case on which two systems have different passing',
            ),
            (
                [
                    make_trial('c', 'acme-large', text='Same.'),
                    make_trial('c', 'acme-small', text='Same.'),
                ],
                'no production case on which two systems have different passing',
            ),
        ],
        ids=['fingerprint', 'one-passing', 'identical'],
    )
    def test_refuses_outputs_it_cannot_publish(
        self, outputs: list[Output], message: str
    ) -> None:
        with pytest.raises(ValueError, match=message):
            prepare(make_votes_family(), [make_case('c')], outputs)

    def test_refuses_cases_a_voter_could_not_tell_apart(self) -> None:
        twin = make_case('first').model_copy(update={'id': 'twin'})

        with pytest.raises(ValueError, match='Cases `first`, `twin` share'):
            prepare(make_votes_family(), [make_case('first'), twin], [])


class TestWrite:
    def test_writes_the_file_and_a_listing_for_review(self, data_dir: Path) -> None:
        published = text_publication()
        outputs = [make_trial('second', 'acme-pro', [Gate.LENGTH])]

        path = publication.write(published, outputs)

        assert path == (
            data_dir / 'publications' / 'writing-fixture' / f'{published.id}.json'
        )
        assert publication.read(path) == published
        listing = path.with_suffix('.md').read_text()
        assert 'Write about first.' in listing
        assert '- Mentions first' in listing
        assert (
            '### Acme Large (`acme-large`): pass\n\n```text\nacme-large on first\n```'
            in listing
        )
        assert '### Acme Pro (`acme-pro`): fail\n\nFailed `length`.' in listing


class TestRead:
    def test_refuses_an_edited_file_naming_both_ids(self, tmp_path: Path) -> None:
        published = text_publication()
        payload = json.loads(published.model_dump_json())
        payload['outputs'][0]['content'] = 'An edited reply.'
        path = tmp_path / 'edited.json'
        path.write_text(json.dumps(payload))
        derived = Publication.model_validate(payload).identity()

        with pytest.raises(ValueError) as raised:
            publication.read(path)

        assert f'derives ID `{derived}`' in str(raised.value)
        assert f'records `{published.id}`' in str(raised.value)


class TestPublish:
    def test_stages_and_activates_the_publication(self) -> None:
        database = arena_database()
        published = text_publication()

        changed = publication.publish(database, published, NOW)

        assert changed
        assert rows(database, 'SELECT family, publication_id FROM active') == [
            ('writing-fixture', published.id)
        ]
        assert rows(database, 'SELECT count(*) FROM cases') == [(2,)]
        assert rows(database, 'SELECT count(*) FROM outputs') == [(6,)]
        assert rows(
            database, "SELECT count(*) FROM outputs WHERE verdict != 'pass'"
        ) == rows(database, 'SELECT count(*) FROM outputs WHERE content IS NULL')

    def test_changes_nothing_for_the_active_publication(self) -> None:
        database = arena_database()
        published = text_publication()
        publication.publish(database, published, NOW)
        recording = RecordingDatabase(database)

        changed = publication.publish(recording, published, NOW)

        assert not changed
        assert len(recording.statements) == 1
        assert recording.statements[0].startswith('SELECT')

    def test_completes_an_interrupted_publish_when_rerun(self) -> None:
        database = arena_database()
        published = text_publication()
        interrupted = RecordingDatabase(
            database, lambda sql: sql.startswith('INSERT INTO outputs')
        )
        with pytest.raises(Interrupted):
            publication.publish(interrupted, published, NOW)

        changed = publication.publish(database, published, NOW)

        assert changed
        assert rows(database, 'SELECT publication_id FROM active') == [(published.id,)]

    def test_refuses_to_activate_incomplete_staging(self) -> None:
        database = arena_database()
        published = text_publication()
        incomplete = published.model_copy(
            update={'outputs': (*published.outputs, published.outputs[0])}
        )

        with pytest.raises(ValueError, match='not completely staged'):
            publication.publish(database, incomplete, NOW)

        assert rows(database, 'SELECT * FROM active') == []

    def test_switches_the_active_publication_and_keeps_the_old_rows(self) -> None:
        database = arena_database()
        first = text_publication()
        publication.publish(database, first, NOW)
        second = prepare(
            make_votes_family(),
            [make_case('first')],
            [make_trial('first', system) for system in ARENA_SYSTEMS],
        )

        publication.publish(database, second, NOW)

        assert rows(database, 'SELECT publication_id FROM active') == [(second.id,)]
        assert rows(database, 'SELECT count(*) FROM publications') == [(2,)]

    def test_refuses_private_cases_before_writing(self) -> None:
        database = arena_database()
        family = make_votes_family(public_voting=False)
        cases = [make_case('private', 'transcript')]
        local = prepare(
            family,
            cases,
            [make_trial('private', system) for system in ARENA_SYSTEMS],
            'local',
        )

        with pytest.raises(ValueError, match='cannot leave this machine'):
            publication.publish(database, local, NOW)

        assert rows(database, 'SELECT count(*) FROM publications') == [(0,)]

    def test_matches_the_committed_publication_fixture(
        self, stale_fixture: Callable[[Path, str], None]
    ) -> None:
        database = arena_database()

        for published in (text_publication(), diff_publication()):
            publication.publish(database, published, NOW)

        dumped = dump(database.connection)
        if (
            not PUBLICATION_FIXTURE.exists()
            or PUBLICATION_FIXTURE.read_text() != dumped
        ):
            stale_fixture(PUBLICATION_FIXTURE, dumped)


def dump(connection: sqlite3.Connection) -> str:
    """Return the rows `publish` writes as SQL that `wrangler d1 execute` loads."""
    lines: list[str] = []
    for table, order in (
        ('publications', 'id'),
        ('cases', 'publication_id, case_key'),
        ('outputs', 'publication_id, output_key'),
        ('active', 'family'),
    ):
        cursor = connection.execute(f'SELECT * FROM {table} ORDER BY {order}')
        columns = ', '.join(column[0] for column in cursor.description)
        lines.extend(
            f'INSERT INTO {table} ({columns}) VALUES ({", ".join(map(literal, row))});'
            for row in cursor
        )
    return '\n'.join(lines) + '\n'


def literal(value: object) -> str:
    match value:
        case None:
            return 'NULL'
        case int():
            return str(value)
        case str():
            return "'" + value.replace("'", "''") + "'"
        case _:
            raise TypeError(value)
