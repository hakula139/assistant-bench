import json
from contextlib import AbstractContextManager, nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from assistant_bench import cli, collection, jobs
from assistant_bench.arena import commands, local, publication
from assistant_bench.catalog import System
from assistant_bench.collection import Collection
from assistant_bench.database import D1Error, SQLiteDatabase
from assistant_bench.families import (
    Case,
    Family,
    TaskTestsSettings,
    VotesSettings,
    load_cases,
    load_family,
)
from assistant_bench.messages import InputError
from assistant_bench.outputs import Output
from support import (
    make_system,
    make_trial,
    record_vote,
    write_votes_family,
)


runner = CliRunner()
NOW = datetime(2026, 10, 29, 12, tzinfo=UTC)
CATEGORIES = {'first': 'reply', 'second': 'docs'}
SYSTEMS = {
    name: make_system(name=name, display_name=f'Public {name.title()}')
    for name in ('large', 'small')
}


def invoke(*args: str) -> Result:
    return runner.invoke(cli.app, ['arena', *args], env={'COLUMNS': '200'})


def collected() -> list[Output]:
    return [
        make_trial(case, system, finish_reason='stop')
        for case in CATEGORIES
        for system in SYSTEMS
    ]


@pytest.fixture
def family(families_dir: Path) -> Family:
    write_votes_family(
        families_dir,
        list(SYSTEMS),
        CATEGORIES,
        name='writing-f',
        samples=2,
        public_voting=True,
    )
    return load_family('writing-f')


@pytest.fixture
def prepared(family: Family) -> Path:
    return publication.write(
        publication.prepare(
            family,
            load_cases(family),
            collected(),
            {name: system.display_name for name, system in SYSTEMS.items()},
            target='production',
        ),
        collected(),
    )


@pytest.fixture
def store(
    arena_store: SQLiteDatabase, monkeypatch: pytest.MonkeyPatch
) -> tuple[SQLiteDatabase, list[str]]:
    """Serve one in-memory store for every target, recording the targets."""
    targets: list[str] = []

    def connect(target: str) -> AbstractContextManager[SQLiteDatabase]:
        targets.append(target)
        return nullcontext(arena_store)

    monkeypatch.setattr(commands, 'connect', connect)
    return arena_store, targets


@pytest.fixture
def voted(prepared: Path, store: tuple[SQLiteDatabase, list[str]]) -> SQLiteDatabase:
    """Publish the prepared file and record two votes from one network tag."""
    database, _ = store
    published = publication.read(prepared)
    publication.publish(
        database, published, NOW - timedelta(days=1), target='production'
    )
    record_vote(
        database,
        published,
        NOW,
        actor='first',
        age=timedelta(hours=2),
        elapsed_ms=2_000,
        ip_tag='shared',
    )
    record_vote(
        database,
        published,
        NOW,
        actor='second',
        age=timedelta(hours=1),
        choice='b',
        elapsed_ms=4_000,
        ip_tag='shared',
        exclusion='spam',
    )
    return database


@pytest.fixture
def collections(monkeypatch: pytest.MonkeyPatch) -> list[Family]:
    """Serve `SYSTEMS` as the catalog and `collected()` as every family's outputs."""
    families: list[Family] = []

    def load_systems(export: object) -> dict[str, System]:
        return SYSTEMS

    def collect(family: Family, systems: dict[str, System]) -> Collection:
        assert systems == SYSTEMS
        families.append(family)
        return Collection(collected(), [])

    monkeypatch.setattr(commands, 'load_export', lambda: None)
    monkeypatch.setattr(commands, 'load_systems', load_systems)
    monkeypatch.setattr(collection, 'collect', collect)
    return families


class TestKnownFamily:
    @pytest.mark.parametrize(
        'args',
        [
            ('votes', 'production', 'writing-x'),
            ('exclude', 'production', 'writing-x', '--vote', '1', '--reason', 'bot'),
            ('restore', 'production', 'writing-x', '--vote', '1'),
            ('status', 'production', 'writing-x'),
        ],
        ids=['votes', 'exclude', 'restore', 'status'],
    )
    @pytest.mark.usefixtures('family')
    def test_refuses_an_unknown_family_with_the_choices(
        self, store: tuple[SQLiteDatabase, list[str]], args: tuple[str, ...]
    ) -> None:
        result = invoke(*args)

        assert result.exit_code == 2
        assert 'No family named `writing-x`. Choose among `writing-f`.' in (
            result.stderr
        )
        assert store[1] == []


class TestPrepare:
    def test_writes_the_publication_and_its_listing_from_current_outputs(
        self, family: Family, data_dir: Path, collections: list[Family]
    ) -> None:
        result = invoke('prepare', 'writing-f')

        assert result.exit_code == 0, result.output
        (path,) = (data_dir / 'publications' / 'writing-f').glob('*.json')
        written = publication.read(path)
        assert written.systems == {'large': 'Public Large', 'small': 'Public Small'}
        assert path.with_suffix('.md').is_file()
        assert f'written to `{path}`' in result.stdout
        assert collections == [family]

    def test_refuses_a_family_without_public_voting(
        self, families_dir: Path, collections: list[Family]
    ) -> None:
        write_votes_family(
            families_dir, list(SYSTEMS), CATEGORIES, name='writing-f', samples=2
        )

        result = invoke('prepare', 'writing-f')

        assert result.exit_code == 1
        assert 'sets no `public_voting`' in result.stderr


class TestPublish:
    def test_activates_the_file_once_and_reports_a_repeat(
        self, prepared: Path, store: tuple[SQLiteDatabase, list[str]]
    ) -> None:
        database, targets = store
        identifier = prepared.stem

        first = invoke('publish', 'staging', str(prepared))
        second = invoke('publish', 'staging', str(prepared))

        assert first.exit_code == second.exit_code == 0, first.output
        assert targets == ['staging', 'staging']
        assert first.stdout == (
            f'Published `writing-f` to `staging`: publication `{identifier}` with '
            '2 cases and 4 outputs.\n'
        )
        assert second.stdout == (
            f'Publication `{identifier}` is already active for `writing-f` in '
            '`staging`, so nothing changed.\n'
        )
        assert database.execute('SELECT publication_id FROM active').rows == [
            {'publication_id': identifier}
        ]

    def test_refuses_an_edited_file_before_connecting(
        self, prepared: Path, store: tuple[SQLiteDatabase, list[str]]
    ) -> None:
        payload = json.loads(prepared.read_text())
        payload['display_name'] = 'Edited'
        prepared.write_text(json.dumps(payload))

        result = invoke('publish', 'production', str(prepared))

        assert result.exit_code == 1
        assert 'was edited' in result.stderr
        assert store[1] == []

    def test_names_a_file_that_is_not_a_publication(
        self, prepared: Path, store: tuple[SQLiteDatabase, list[str]]
    ) -> None:
        prepared.write_text('{')

        result = invoke('publish', 'production', str(prepared))

        assert (result.exit_code, result.stderr) == (
            1,
            f'Publication `{prepared}`: Invalid JSON: EOF while parsing an object at '
            'line 1 column 1.\n',
        )
        assert store[1] == []

    def test_prints_what_d1_refused(
        self, prepared: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def refuse(target: str) -> AbstractContextManager[SQLiteDatabase]:
            raise D1Error('D1 refused the statement with HTTP 403. Forbidden.')

        monkeypatch.setattr(commands, 'connect', refuse)

        result = invoke('publish', 'production', str(prepared))

        assert result.exit_code == 1
        assert result.stderr == 'D1 refused the statement with HTTP 403. Forbidden.\n'

    def test_rejects_an_unknown_target_by_name(self, prepared: Path) -> None:
        result = invoke('publish', 'prod', str(prepared))

        assert result.exit_code == 2
        assert "'staging'" in result.stderr and "'production'" in result.stderr


class TestServe:
    @pytest.mark.parametrize('change', ['removed', 'tests', 'no-pairs'])
    @pytest.mark.usefixtures('collections')
    def test_synchronizes_active_families_while_another_remains_pairable(
        self,
        change: str,
        family: Family,
        arena_store: SQLiteDatabase,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        assert isinstance(family.settings, VotesSettings)
        selected = family.model_copy(
            update={
                'settings': family.settings.model_copy(update={'public_voting': False})
            }
        )
        changed = False

        def names() -> list[str]:
            return ['kept'] if changed and change == 'removed' else ['kept', 'old']

        def load(name: str) -> Family:
            settings = selected.settings
            if changed and name == 'old' and change == 'tests':
                settings = TaskTestsSettings.model_validate(
                    family.settings.model_dump(
                        exclude={
                            'public_voting',
                            'reference',
                            'render',
                            'language',
                            'gates',
                            'defects',
                        }
                    )
                    | {'scorer': 'tests'}
                )
            return selected.model_copy(update={'name': name, 'settings': settings})

        def has_jobs(item: Family) -> bool:
            return not (changed and item.name == 'old' and change == 'no-pairs')

        cases = load_cases(family)

        def load_selected_cases(item: Family) -> list[Case]:
            return cases

        def keys() -> dict[str, str]:
            return {}

        def serve(signing: dict[str, str]) -> None:
            pass

        monkeypatch.setattr(commands, 'family_names', names)
        monkeypatch.setattr(commands, 'load_family', load)
        monkeypatch.setattr(commands, 'load_cases', load_selected_cases)
        monkeypatch.setattr(jobs, 'has_jobs', has_jobs)
        monkeypatch.setattr(local, 'build', lambda: None)
        monkeypatch.setattr(local, 'migrate', lambda: None)
        monkeypatch.setattr(local, 'database', lambda: nullcontext(arena_store))
        monkeypatch.setattr(local, 'keys', keys)
        monkeypatch.setattr(local, 'serve', serve)

        first = runner.invoke(cli.app, ['serve'])
        assert first.exit_code == 0, first.output
        assert arena_store.execute(
            'SELECT family FROM active ORDER BY family'
        ).rows == [
            {'family': 'kept'},
            {'family': 'old'},
        ]
        changed = True
        second = runner.invoke(cli.app, ['serve'])

        assert second.exit_code == 0, second.output
        assert arena_store.execute('SELECT family FROM active').rows == [
            {'family': 'kept'},
        ]

    @pytest.mark.usefixtures('collections')
    def test_serves_local_content_and_deactivates_a_family_that_loses_its_pairs(
        self,
        family: Family,
        arena_store: SQLiteDatabase,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        assert isinstance(family.settings, VotesSettings)
        selected = family.model_copy(
            update={
                'settings': family.settings.model_copy(update={'public_voting': False})
            }
        )
        calls: list[str] = []
        generated = True

        def load_family(name: str) -> Family:
            return selected

        def has_jobs(selected: Family) -> bool:
            return generated

        monkeypatch.setattr(commands, 'load_family', load_family)
        monkeypatch.setattr(jobs, 'has_jobs', has_jobs)
        monkeypatch.setattr(local, 'build', lambda: calls.append('build'))
        monkeypatch.setattr(local, 'migrate', lambda: calls.append('migrate'))
        monkeypatch.setattr(local, 'database', lambda: nullcontext(arena_store))
        monkeypatch.setattr(local, 'keys', lambda: {'ACTOR_KEY': 'synthetic-key'})

        def serve(keys: dict[str, str]) -> None:
            calls.append('serve')
            assert keys == {'ACTOR_KEY': 'synthetic-key'}
            assert arena_store.execute('SELECT split FROM cases').rows == [
                {'split': 'public'},
                {'split': 'public'},
            ]
            with pytest.raises(InputError, match='Stop `bench serve`'), local.lock():
                pytest.fail('The server released its lock before exiting.')

        monkeypatch.setattr(local, 'serve', serve)

        first = runner.invoke(cli.app, ['serve'])
        assert first.exit_code == 0, first.output
        generated = False
        second = runner.invoke(cli.app, ['serve'])

        assert second.exit_code == 1
        assert 'No local case has two different passing outputs' in second.stderr
        assert arena_store.execute('SELECT family FROM active').rows == []
        assert calls == ['build', 'migrate', 'serve', 'build', 'migrate']

    @pytest.mark.parametrize('failure', ['collection', 'build', 'migration'])
    @pytest.mark.usefixtures('collections')
    def test_stops_preparation_failures_before_starting_the_server(
        self,
        failure: str,
        family: Family,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls: list[str] = []

        def fail(*args: object) -> None:
            raise InputError('The preparation input is invalid.')

        def has_jobs(selected: Family) -> bool:
            return True

        def serve(keys: dict[str, str]) -> None:
            pytest.fail('Server started.')

        monkeypatch.setattr(jobs, 'has_jobs', has_jobs)
        monkeypatch.setattr(local, 'build', lambda: calls.append('build'))
        monkeypatch.setattr(local, 'migrate', lambda: calls.append('migration'))
        monkeypatch.setattr(local, 'serve', serve)
        if failure == 'collection':
            monkeypatch.setattr(collection, 'collect', fail)
        else:
            monkeypatch.setattr(
                local, 'build' if failure == 'build' else 'migrate', fail
            )

        result = runner.invoke(cli.app, ['serve'])

        assert result.exit_code == 1
        assert result.stderr == 'The preparation input is invalid.\n'
        assert calls == (['build'] if failure == 'migration' else [])
        with local.lock():
            pass


class TestStatus:
    def test_summarizes_the_active_families(
        self, prepared: Path, store: tuple[SQLiteDatabase, list[str]]
    ) -> None:
        database, _ = store
        published = publication.read(prepared)
        now = datetime.now(UTC)
        publication.publish(database, published, now, target='production')
        record_vote(database, published, now, actor='voter', age=timedelta(hours=1))

        result = invoke('status', 'production', 'writing-f')

        assert result.exit_code == 0, result.output
        assert result.stdout.startswith(
            f'Family `writing-f`: publication `{published.id}` active since '
        )
        assert 'with 2 cases and 4 outputs. 1 counted vote from 1 voter,' in (
            result.stdout
        )

    @pytest.mark.parametrize(
        ('args', 'code', 'message'),
        [
            (
                ('status', 'staging'),
                0,
                'No family has an active publication in `staging`.\n',
            ),
            (
                ('status', 'staging', 'writing-f'),
                1,
                'Family `writing-f` has no active publication in `staging`.\n',
            ),
        ],
        ids=['every', 'named'],
    )
    @pytest.mark.usefixtures('family')
    def test_says_when_no_family_is_active(
        self,
        store: tuple[SQLiteDatabase, list[str]],
        args: tuple[str, ...],
        code: int,
        message: str,
    ) -> None:
        result = invoke(*args)

        assert result.exit_code == code
        assert result.output == message


class TestListVotes:
    def test_summarizes_each_network_tag(self, voted: SQLiteDatabase) -> None:
        result = invoke('votes', 'production', 'writing-f')

        assert result.exit_code == 0, result.output
        assert result.stdout == (
            'IP tag  Votes  Actors  A    B    Tie  Both bad  Median time  '
            'First vote            Last vote             Excluded\n'
            'shared  2      2       50%  50%  0%   0%        3.0 s        '
            '2026-10-29 10:00 UTC  2026-10-29 11:00 UTC  1\n'
        )

    def test_summarizes_each_browser(self, voted: SQLiteDatabase) -> None:
        result = invoke('votes', 'production', 'writing-f', '--by', 'actor')

        assert result.exit_code == 0, result.output
        assert result.stdout.splitlines()[1:] == [
            'first   1      1        100%  0%    0%   0%        2.0 s        '
            '2026-10-29 10:00 UTC  2026-10-29 10:00 UTC  0',
            'second  1      1        0%    100%  0%   0%        4.0 s        '
            '2026-10-29 11:00 UTC  2026-10-29 11:00 UTC  1',
        ]

    def test_lists_the_selected_browsers_votes_with_their_reasons(
        self, voted: SQLiteDatabase
    ) -> None:
        result = invoke('votes', 'production', 'writing-f', '--actor', 'second')

        assert result.exit_code == 0, result.output
        assert result.stdout == (
            'Vote  Recorded              Actor   IP tag  A      B      Choice  Time   '
            'Exclusion\n'
            '2     2026-10-29 11:00 UTC  second  shared  large  small  B       4.0 s  '
            'spam\n'
        )

    @pytest.mark.parametrize(
        'args', [(), ('--ip-tag', 'unknown')], ids=['summary', 'selector']
    )
    @pytest.mark.usefixtures('family')
    def test_says_when_no_vote_matches(
        self, store: tuple[SQLiteDatabase, list[str]], args: tuple[str, ...]
    ) -> None:
        result = invoke('votes', 'production', 'writing-f', *args)

        assert result.exit_code == 0
        assert result.stdout == 'No vote of `writing-f` in `production` matches.\n'


class TestExclude:
    def test_excludes_the_selected_votes_and_reports_the_new_ones(
        self, voted: SQLiteDatabase
    ) -> None:
        result = invoke(
            'exclude',
            'production',
            'writing-f',
            '--ip-tag',
            'shared',
            '--reason',
            'bot',
        )

        assert result.exit_code == 0, result.output
        assert result.stdout == 'Excluded 1 vote of `writing-f` in `production`.\n'
        assert voted.execute('SELECT exclusion FROM votes ORDER BY id').rows == [
            {'exclusion': 'bot'},
            {'exclusion': 'spam'},
        ]


class TestRestore:
    def test_restores_the_selected_votes_and_reports_the_count(
        self, voted: SQLiteDatabase
    ) -> None:
        result = invoke(
            'restore', 'production', 'writing-f', '--vote', '1', '--vote', '2'
        )

        assert result.exit_code == 0, result.output
        assert result.stdout == 'Restored 1 vote of `writing-f` in `production`.\n'
        assert voted.execute(
            'SELECT count(*) AS excluded FROM votes WHERE exclusion IS NOT NULL'
        ).rows == [{'excluded': 0}]


class TestSelector:
    @pytest.mark.parametrize(
        ('args', 'message'),
        [
            (
                ('exclude', 'production', 'writing-f', '--reason', 'bot'),
                'Pass one of `--ip-tag`, `--actor` or `--vote`.\n',
            ),
            (
                ('restore', 'production', 'writing-f'),
                'Pass one of `--ip-tag`, `--actor` or `--vote`.\n',
            ),
            (
                ('restore', 'production', 'writing-f', '--actor', 'a', '--vote', '1'),
                'Pass only one of `--actor`, `--vote`.\n',
            ),
            (
                ('votes', 'production', 'writing-f', '--ip-tag', 't', '--actor', 'a'),
                'Pass only one of `--ip-tag`, `--actor`.\n',
            ),
            (
                ('votes', 'production', 'writing-f', '--by', 'ip', '--actor', 'a'),
                'Pass `--by` only to summarize, without `--ip-tag` or `--actor`.\n',
            ),
            (
                ('exclude', 'production', 'writing-f', '--vote', '1', '--reason', ' '),
                'Give a reason for the exclusion.\n',
            ),
        ],
        ids=[
            'exclude-none',
            'restore-none',
            'restore-two',
            'votes-two',
            'votes-by',
            'exclude-blank-reason',
        ],
    )
    @pytest.mark.usefixtures('family')
    def test_refuses_invalid_options_before_connecting(
        self,
        store: tuple[SQLiteDatabase, list[str]],
        args: tuple[str, ...],
        message: str,
    ) -> None:
        result = invoke(*args)

        assert result.exit_code == 1
        assert result.stderr == message
        assert store[1] == []


class TestFail:
    @pytest.mark.parametrize(
        'args',
        [
            ('votes', 'production', 'writing-f'),
            ('exclude', 'production', 'writing-f', '--vote', '1', '--reason', 'bot'),
            ('restore', 'production', 'writing-f', '--vote', '1'),
        ],
        ids=['votes', 'exclude', 'restore'],
    )
    @pytest.mark.usefixtures('family')
    def test_prints_what_d1_refused(
        self, monkeypatch: pytest.MonkeyPatch, args: tuple[str, ...]
    ) -> None:
        def refuse(target: str) -> AbstractContextManager[SQLiteDatabase]:
            raise D1Error('D1 refused the statement with HTTP 403. Forbidden.')

        monkeypatch.setattr(commands, 'connect', refuse)

        result = invoke(*args)

        assert result.exit_code == 1
        assert result.stderr == 'D1 refused the statement with HTTP 403. Forbidden.\n'
