import json
import sqlite3
from collections.abc import Callable, Sequence
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from assistant_bench.arena import publication, votes
from assistant_bench.arena.d1 import Param, Result, SQLiteDatabase
from assistant_bench.arena.publication import Publication
from assistant_bench.families import (
    Case,
    Family,
    Gate,
)
from assistant_bench.messages import InputError
from assistant_bench.outputs import Output
from support import (
    ARENA_NAMES,
    ARENA_SYSTEMS,
    CODE_DEFECTS,
    FIXTURES,
    MIGRATIONS_DIR,
    make_case,
    make_trial,
    make_votes_family,
    record_vote,
    with_settings,
)


PUBLICATION_FIXTURE = FIXTURES / 'arena-publication.sql'
NOW = datetime(2026, 10, 29, 12, tzinfo=UTC)


def prepare(
    family: Family, cases: Sequence[Case], outputs: Sequence[Output]
) -> Publication:
    return publication.prepare(family, cases, outputs, ARENA_NAMES, target='production')


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


class TestPrepare:
    def test_preserves_html_content_and_unknown_lengths_through_the_vote_snapshot(
        self, arena_store: SQLiteDatabase
    ) -> None:
        family = with_settings(make_votes_family(), render='html')
        case = make_case('page')
        source = (FIXTURES / 'html' / 'community-event.html').read_text()
        outputs = [
            make_trial('page', system, text=source.replace('Welcome', system))
            for system in ARENA_SYSTEMS[:2]
        ]
        outputs[1] = outputs[1].model_copy(
            update={'text': source.replace('Saturday', 'Sunday')}
        )
        outputs.append(
            make_trial('page', ARENA_SYSTEMS[2], [Gate.HTML], text='raw failure')
        )

        published = prepare(family, [case], outputs)
        publication.publish(arena_store, published, NOW, target='production')
        passing = published.model_copy(
            update={
                'outputs': tuple(
                    row for row in published.outputs if row.verdict == 'pass'
                )
            }
        )
        record_vote(arena_store, passing, NOW, actor='reader', age=timedelta(hours=1))
        cut = votes.read_cut(arena_store, family.name, NOW)

        assert published.render == 'html'
        expected = {output.system: output.text for output in outputs}
        for output in published.outputs:
            assert output.length is None
            assert output.content == (
                expected[output.system] if output.verdict == 'pass' else None
            )
        assert cut is not None
        assert cut.inventory.outputs == tuple(
            publication.OutputFacts.model_validate(
                output.model_dump(exclude={'content'})
            )
            for output in published.outputs
        )
        analysis = votes.voting_input(
            votes.Snapshot(
                family=family.name, taken_at=NOW, production=cut, local=None
            ),
            family,
            outputs,
        )
        assert [(row.length_a, row.length_b) for row in analysis.ordinary] == [
            (None, None)
        ]

    @pytest.mark.parametrize(
        'fenced', [False, True], ids=['ineligible', 'uncollected-fence']
    )
    def test_refuses_passing_html_that_collection_has_not_made_eligible(
        self, fenced: bool
    ) -> None:
        family = with_settings(make_votes_family(), render='html')
        source = (FIXTURES / 'html' / 'community-event.html').read_text()
        reply = f'```html\n{source}```\n' if fenced else source.replace('</body>', '')
        outputs = [make_trial('page', system, text=reply) for system in ARENA_SYSTEMS]

        with pytest.raises(InputError, match='not a collected eligible document'):
            prepare(family, [make_case('page')], outputs)

    @pytest.mark.parametrize('public_voting', [True, False])
    def test_partitions_local_cases_and_records_their_split(
        self, public_voting: bool
    ) -> None:
        family = make_votes_family(public_voting=public_voting)
        cases = [
            make_case('open', 'synthetic'),
            make_case('quoted', 'public'),
            make_case('private', 'transcript'),
            make_case('secret', 'held-out'),
        ]
        outputs = [
            make_trial(case.id, system) for case in cases for system in ARENA_SYSTEMS
        ]
        names = {publication.case_key(case): case.id for case in cases}

        local = publication.prepare(family, cases, outputs, ARENA_NAMES, target='local')

        expected = {'private': 'private', 'secret': 'held-out'}
        if not public_voting:
            expected |= {'open': 'public', 'quoted': 'public'}
        assert {names[case.key]: case.split for case in local.cases} == expected
        assert {row.case_key for row in local.outputs} == {
            case.key for case in local.cases
        }
        assert local.target == 'local'
        assert (
            local.identity()
            != local.model_copy(update={'target': 'production'}).identity()
        )

    def test_refuses_duplicate_cases_across_vote_stores(self) -> None:
        first = make_case('first')
        second = first.model_copy(update={'id': 'second', 'source': 'held-out'})
        outputs = [make_trial('first', system) for system in ARENA_SYSTEMS]

        with pytest.raises(InputError, match='share an instruction and checklist'):
            prepare(make_votes_family(), [first, second], outputs)

    def test_publishes_only_the_public_and_synthetic_cases(self) -> None:
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

        production = prepare(family, cases, outputs)

        assert sorted(by_key[case.key] for case in production.cases) == [
            'open',
            'quoted',
        ]
        assert {output.case_key for output in production.outputs} == {
            case.key for case in production.cases
        }

    def test_refuses_a_family_without_public_voting(self) -> None:
        family = make_votes_family(public_voting=False)
        cases = [make_case('open')]
        outputs = [make_trial('open', system) for system in ARENA_SYSTEMS]

        with pytest.raises(ValueError, match='sets no `public_voting`'):
            prepare(family, cases, outputs)

    def test_publishes_task_diffs_with_verdicts_from_the_tests_gate(self) -> None:
        published = diff_publication()

        outputs = {output.system: output for output in published.outputs}
        assert published.render == 'diff'
        assert published.defects == CODE_DEFECTS
        assert outputs['acme-large'].verdict == 'pass'
        assert outputs['acme-large'].content == '--- a/x\n+++ b/x\n+fixed\n'
        assert outputs['acme-pro'].verdict == 'fail'
        assert outputs['acme-pro'].content is None

    def test_grades_an_errored_task_trial_by_its_tests_and_an_untested_one_not_at_all(
        self,
    ) -> None:
        family = make_votes_family('coding-fixture', diff=True)

        published = prepare(
            family,
            [make_case('fix-parser', category='bug-fix')],
            [
                make_trial(
                    'fix-parser', 'acme-large', text='--- a/x\n+++ b/x\n+fixed\n'
                ),
                make_trial(
                    'fix-parser',
                    'acme-small',
                    error='AgentTimeoutError',
                    reward=1.0,
                    text='--- a/x\n+++ b/x\n+late\n',
                ),
                make_trial(
                    'fix-parser',
                    'acme-pro',
                    [Gate.TESTS],
                    error='AgentTimeoutError',
                    reward=0.0,
                    text='--- a/x\n+++ b/x\n',
                ),
                make_trial(
                    'fix-parser',
                    'acme-pro',
                    gate_failures=None,
                    error='RuntimeError',
                    reward=None,
                    text=None,
                    trial='fix-parser__acme-pro__2',
                ),
            ],
        )

        verdicts = sorted(
            (output.system, output.verdict, output.errored, output.content)
            for output in published.outputs
        )
        assert verdicts == [
            ('acme-large', 'pass', False, '--- a/x\n+++ b/x\n+fixed\n'),
            ('acme-pro', 'fail', True, None),
            ('acme-pro', 'ungraded', True, None),
            ('acme-small', 'pass', True, '--- a/x\n+++ b/x\n+late\n'),
        ]

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

    def test_freezes_the_reference_and_names_it_without_outputs(self) -> None:
        published = prepare(
            make_votes_family(),
            [make_case('c')],
            [make_trial('c', 'acme-small'), make_trial('c', 'acme-pro')],
        )

        assert published.reference == 'acme-large'
        assert published.systems == ARENA_NAMES
        assert {output.system for output in published.outputs} == {
            'acme-small',
            'acme-pro',
        }

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
            (
                [
                    make_trial('c', 'acme-large', response_models=('large-2026',)),
                    make_trial(
                        'c',
                        'acme-large',
                        job='job-2',
                        trial='c__2',
                        response_models=('other-2026',),
                    ),
                    make_trial('c', 'acme-small'),
                ],
                'System `acme-large` reported several models',
            ),
        ],
        ids=['fingerprint', 'one-passing', 'identical', 'several-models'],
    )
    def test_refuses_outputs_it_cannot_publish(
        self, outputs: list[Output], message: str
    ) -> None:
        with pytest.raises(ValueError, match=message):
            prepare(make_votes_family(), [make_case('c')], outputs)

    def test_checks_only_the_outputs_it_publishes_for_several_models(self) -> None:
        cases = [make_case('c'), make_case('secret', 'held-out')]
        outputs = [
            make_trial('c', 'acme-large', response_models=('large-2026',)),
            make_trial('c', 'acme-small'),
            make_trial('secret', 'acme-large', response_models=('other-2026',)),
        ]

        production = prepare(make_votes_family(), cases, outputs)

        assert [case.key for case in production.cases] == [
            publication.case_key(cases[0])
        ]

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
    @pytest.mark.parametrize('invalid', ['length', 'ineligible', 'fenced'])
    def test_refuses_self_consistent_html_files_that_break_the_document_contract(
        self, tmp_path: Path, invalid: str
    ) -> None:
        source = (FIXTURES / 'html' / 'community-event.html').read_text()
        published = prepare(
            with_settings(make_votes_family(), render='html'),
            [make_case('page')],
            [
                make_trial(
                    'page', system, text=source.replace('<title>', f'<title>{index} ')
                )
                for index, system in enumerate(ARENA_SYSTEMS)
            ],
        )
        first = published.outputs[0]
        assert first.content is not None
        if invalid == 'length':
            changed = first.model_copy(update={'length': 10})
            message = 'HTML outputs have no text length'
        else:
            content = (
                f'```html\n{first.content}```\n'
                if invalid == 'fenced'
                else first.content.replace('</body>', '')
            )
            changed = first.model_copy(update={'content': content})
            message = 'a passing HTML output must be an eligible document'
        draft = published.model_copy(
            update={'outputs': (changed, *published.outputs[1:])}
        )
        invalid_file = draft.model_copy(update={'id': draft.identity()})
        path = tmp_path / 'publication.json'
        path.write_text(invalid_file.model_dump_json())

        with pytest.raises(ValueError, match=message):
            publication.read(path)

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
    def test_local_private_content_never_reaches_a_remote_store(
        self, arena_store: SQLiteDatabase
    ) -> None:
        family = make_votes_family()
        cases = [make_case('private', 'transcript'), make_case('hidden', 'held-out')]
        prepared = publication.prepare(
            family,
            cases,
            [make_trial(case.id, system) for case in cases for system in ARENA_SYSTEMS],
            ARENA_NAMES,
            target='local',
        )
        remote = RecordingDatabase(arena_store)

        for target in ('staging', 'production'):
            with pytest.raises(InputError, match='cannot be published'):
                publication.publish(remote, prepared, NOW, target=target)
        assert remote.statements == []
        changed = publication.publish(arena_store, prepared, NOW, target='local')

        assert changed
        assert rows(arena_store, 'SELECT split FROM cases ORDER BY split') == [
            ('held-out',),
            ('private',),
        ]
        assert rows(arena_store, 'SELECT content FROM outputs ORDER BY content') == [
            (row.content,)
            for row in sorted(prepared.outputs, key=lambda row: row.content or '')
        ]

    def test_stages_and_activates_the_publication(
        self, arena_store: SQLiteDatabase
    ) -> None:
        database = arena_store
        published = text_publication()

        changed = publication.publish(database, published, NOW, target='production')

        assert changed
        assert rows(database, 'SELECT family, publication_id FROM active') == [
            ('writing-fixture', published.id)
        ]
        assert rows(database, 'SELECT count(*) FROM cases') == [(2,)]
        assert rows(database, 'SELECT count(*) FROM outputs') == [(6,)]
        assert rows(
            database, "SELECT count(*) FROM outputs WHERE verdict != 'pass'"
        ) == rows(database, 'SELECT count(*) FROM outputs WHERE content IS NULL')

    def test_stages_only_comparable_cases_once_for_each_system_pair(
        self, arena_store: SQLiteDatabase
    ) -> None:
        cases = [make_case(name) for name in ('first', 'second', 'same', 'alone')]
        published = prepare(
            make_votes_family(),
            cases,
            [
                make_trial('first', 'acme-large', text='Shared.'),
                make_trial(
                    'first', 'acme-large', text='Distinct.', trial='first__large__2'
                ),
                make_trial('first', 'acme-small', text='Shared.'),
                make_trial(
                    'first',
                    'acme-small',
                    text='Small distinct.',
                    trial='first__small__2',
                ),
                make_trial('first', 'acme-pro', [Gate.EMPTY], text=''),
                *(make_trial('second', system) for system in ARENA_SYSTEMS),
                make_trial('same', 'acme-large', text='Same.'),
                make_trial('same', 'acme-small', text='Same.'),
                make_trial('alone', 'acme-large'),
                make_trial(
                    'alone',
                    'acme-small',
                    gate_failures=None,
                    error='RuntimeError',
                    reward=None,
                    text=None,
                ),
            ],
        )
        keys = {case.id: publication.case_key(case) for case in cases}

        publication.publish(arena_store, published, NOW, target='production')

        facts = rows(
            arena_store,
            'SELECT low_system, high_system, case_keys '
            'FROM publication_pair_cases ORDER BY low_system, high_system',
        )
        assert [
            (low, high, json.loads(keys_json)) for low, high, keys_json in facts
        ] == [
            ('acme-large', 'acme-pro', [keys['second']]),
            ('acme-large', 'acme-small', sorted([keys['first'], keys['second']])),
            ('acme-pro', 'acme-small', [keys['second']]),
        ]

    def test_splits_inserts_within_the_parameter_limit(
        self, arena_store: SQLiteDatabase
    ) -> None:
        cases = [make_case(f'case-{index}') for index in range(25)]
        published = prepare(
            make_votes_family(),
            cases,
            [make_trial(case.id, system) for case in cases for system in ARENA_SYSTEMS],
        )
        recording = RecordingDatabase(arena_store)

        publication.publish(recording, published, NOW, target='production')

        inserts = [sql.split()[2] for sql in recording.statements if 'VALUES' in sql]
        assert inserts.count('cases') == 2
        assert inserts.count('outputs') == 8
        assert rows(recording.database, 'SELECT count(*) FROM outputs') == [(75,)]

    def test_changes_nothing_for_the_active_publication(
        self, arena_store: SQLiteDatabase
    ) -> None:
        database = arena_store
        published = text_publication()
        publication.publish(database, published, NOW, target='production')
        recording = RecordingDatabase(database)

        changed = publication.publish(recording, published, NOW, target='production')

        assert not changed
        assert len(recording.statements) == 1
        assert recording.statements[0].startswith('SELECT')

    @pytest.mark.parametrize(
        'statement',
        [
            'INSERT INTO outputs',
            'INSERT INTO publication_pair_cases',
            'INSERT INTO active',
        ],
        ids=['outputs', 'pair-facts', 'activation'],
    )
    def test_completes_an_interrupted_publish_when_rerun(
        self, arena_store: SQLiteDatabase, statement: str
    ) -> None:
        database = arena_store
        published = text_publication()
        interrupted = RecordingDatabase(database, lambda sql: statement in sql)
        with pytest.raises(Interrupted):
            publication.publish(interrupted, published, NOW, target='production')

        changed = publication.publish(database, published, NOW, target='production')

        assert changed
        assert rows(database, 'SELECT publication_id FROM active') == [(published.id,)]

    @pytest.mark.parametrize('change', ['missing', 'extra'])
    def test_refuses_incomplete_or_extra_pair_facts_before_activation(
        self, arena_store: SQLiteDatabase, monkeypatch: pytest.MonkeyPatch, change: str
    ) -> None:
        published = text_publication()
        execute = arena_store.execute
        first = publication.case_key(make_case('first'))
        same = publication.case_key(make_case('second', category='reply'))

        def changed(sql: str, params: Sequence[Param] = ()) -> Result:
            if 'INSERT INTO active' in sql:
                arena_store.connection.execute(
                    'UPDATE publication_pair_cases SET case_keys = ? '
                    "WHERE low_system = 'acme-large' AND high_system = 'acme-small'",
                    [json.dumps([] if change == 'missing' else [first, same])],
                )
            return execute(sql, params)

        monkeypatch.setattr(arena_store, 'execute', changed)

        with pytest.raises(InputError, match='not completely staged'):
            publication.publish(arena_store, published, NOW, target='production')

        assert rows(arena_store, 'SELECT * FROM active') == []

    def test_refuses_to_activate_incomplete_staging(
        self, arena_store: SQLiteDatabase
    ) -> None:
        database = arena_store
        published = text_publication()
        incomplete = published.model_copy(
            update={'outputs': (*published.outputs, published.outputs[0])}
        )

        with pytest.raises(ValueError, match='not completely staged'):
            publication.publish(database, incomplete, NOW, target='production')

        assert rows(database, 'SELECT * FROM active') == []

    def test_switches_the_active_publication_and_keeps_the_old_rows(
        self, arena_store: SQLiteDatabase
    ) -> None:
        database = arena_store
        first = text_publication()
        publication.publish(database, first, NOW, target='production')
        second = prepare(
            make_votes_family(),
            [make_case('first')],
            [make_trial('first', system) for system in ARENA_SYSTEMS],
        )

        publication.publish(database, second, NOW, target='production')

        assert rows(database, 'SELECT publication_id FROM active') == [(second.id,)]
        assert rows(database, 'SELECT count(*) FROM publications') == [(2,)]

    def test_refuses_private_cases_before_writing(
        self, arena_store: SQLiteDatabase
    ) -> None:
        database = arena_store
        published = text_publication()
        cases = tuple(
            case.model_copy(update={'source': 'transcript'}) for case in published.cases
        )
        draft = published.model_copy(update={'cases': cases})
        private = draft.model_copy(update={'id': draft.identity()})

        with pytest.raises(ValueError, match='cannot leave this machine'):
            publication.publish(database, private, NOW, target='production')

        assert rows(database, 'SELECT count(*) FROM publications') == [(0,)]

    def test_matches_the_committed_publication_fixture(
        self,
        arena_store: SQLiteDatabase,
        stale_fixture: Callable[[Path, str], None],
    ) -> None:
        database = arena_store

        for published in (text_publication(), diff_publication()):
            publication.publish(database, published, NOW, target='production')

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
        ('publication_pair_cases', 'publication_id, low_system, high_system'),
        ('active', 'family'),
    ):
        cursor = connection.execute(f'SELECT * FROM {table} ORDER BY {order}')
        columns = ', '.join(column[0] for column in cursor.description)
        lines.extend(
            f'INSERT INTO {table} ({columns}) VALUES ({", ".join(map(literal, row))});'
            for row in cursor
        )
    return '\n'.join(lines) + '\n'


class TestPublicationPairCases:
    def test_backfills_existing_publications_without_changing_their_rows(
        self, arena_store: SQLiteDatabase
    ) -> None:
        for published in (text_publication(), diff_publication()):
            publication.publish(arena_store, published, NOW, target='production')
        existing = (
            '\n'.join(
                line
                for line in dump(arena_store.connection).splitlines()
                if not line.startswith('INSERT INTO publication_pair_cases')
            )
            + '\n'
        )
        expected = rows(
            arena_store,
            'SELECT * FROM publication_pair_cases '
            'ORDER BY publication_id, low_system, high_system',
        )

        with closing(sqlite3.connect(':memory:', isolation_level=None)) as connection:
            for path in sorted(MIGRATIONS_DIR.glob('*.sql')):
                if path.name < '0004_':
                    connection.executescript(path.read_text())
            connection.executescript(existing)

            connection.executescript(
                (MIGRATIONS_DIR / '0004_publication_pair_cases.sql').read_text()
            )

            assert (
                rows(
                    SQLiteDatabase(connection),
                    'SELECT * FROM publication_pair_cases '
                    'ORDER BY publication_id, low_system, high_system',
                )
                == expected
            )
            for table, order in (
                ('publications', 'id'),
                ('cases', 'publication_id, case_key'),
                ('outputs', 'publication_id, output_key'),
                ('active', 'family'),
            ):
                current = arena_store.connection.execute(
                    f'SELECT * FROM {table} ORDER BY {order}'
                )
                columns = ', '.join(column[0] for column in current.description)
                preserved = connection.execute(
                    f'SELECT {columns} FROM {table} ORDER BY {order}'
                )
                assert preserved.fetchall() == current.fetchall(), table

    def test_completes_staging_after_backfilling_a_partial_inactive_publication(
        self, arena_store: SQLiteDatabase
    ) -> None:
        cases = [make_case('first'), make_case('second')]
        published = prepare(
            make_votes_family(),
            cases,
            [make_trial(case.id, system) for case in cases for system in ARENA_SYSTEMS],
        )
        publication.publish(arena_store, published, NOW, target='production')
        staged = '\n'.join(
            line
            for line in dump(arena_store.connection).splitlines()
            if not line.startswith(
                ('INSERT INTO publication_pair_cases', 'INSERT INTO active')
            )
        )
        keys = sorted(publication.case_key(case) for case in cases)

        with closing(sqlite3.connect(':memory:', isolation_level=None)) as connection:
            for path in sorted(MIGRATIONS_DIR.glob('*.sql')):
                if path.name < '0004_':
                    connection.executescript(path.read_text())
            connection.executescript(staged)
            connection.execute(
                'DELETE FROM outputs WHERE case_key = ?',
                [publication.case_key(cases[1])],
            )
            connection.executescript(
                (MIGRATIONS_DIR / '0004_publication_pair_cases.sql').read_text()
            )
            database = SQLiteDatabase(connection)

            changed = publication.publish(database, published, NOW, target='production')

            assert changed
            assert rows(database, 'SELECT publication_id FROM active') == [
                (published.id,)
            ]
            assert [
                (low, high, json.loads(case_keys))
                for low, high, case_keys in rows(
                    database,
                    'SELECT low_system, high_system, case_keys '
                    'FROM publication_pair_cases ORDER BY low_system, high_system',
                )
            ] == [
                ('acme-large', 'acme-pro', keys),
                ('acme-large', 'acme-small', keys),
                ('acme-pro', 'acme-small', keys),
            ]


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
