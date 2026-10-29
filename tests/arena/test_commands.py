import json
from contextlib import AbstractContextManager, nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from assistant_bench import jobs
from assistant_bench.arena import commands, publication
from assistant_bench.arena.d1 import D1Error
from assistant_bench.catalog import System
from assistant_bench.families import Family, load_cases, load_family
from assistant_bench.outputs import Output
from support import (
    SQLiteDatabase,
    arena_database,
    make_system,
    passing_outputs,
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
    return runner.invoke(commands.app, list(args), env={'COLUMNS': '200'})


def collected() -> list[Output]:
    return list(passing_outputs(CATEGORIES, SYSTEMS).values())


@pytest.fixture
def family(families_dir: Path) -> Family:
    write_votes_family(
        families_dir, 'writing-f', list(SYSTEMS), CATEGORIES, public_voting=True
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
            'production',
        ),
        collected(),
    )


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> tuple[SQLiteDatabase, list[str]]:
    """Serve one in-memory store for every target, recording the targets."""
    database = arena_database()
    targets: list[str] = []

    def connect(target: str) -> AbstractContextManager[SQLiteDatabase]:
        targets.append(target)
        return nullcontext(database)

    monkeypatch.setattr(commands, 'connect', connect)
    return database, targets


@pytest.fixture
def voted(prepared: Path, store: tuple[SQLiteDatabase, list[str]]) -> SQLiteDatabase:
    """Publish the prepared file and record two votes from one network tag."""
    database, _ = store
    published = publication.read(prepared)
    publication.publish(database, published, NOW - timedelta(days=1))
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

    def collect(family: Family, systems: dict[str, System]) -> list[Output]:
        assert systems == SYSTEMS
        families.append(family)
        return collected()

    monkeypatch.setattr(commands, 'load_export', lambda: None)
    monkeypatch.setattr(commands, 'load_systems', load_systems)
    monkeypatch.setattr(jobs, 'collect', collect)
    return families


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
        write_votes_family(families_dir, 'writing-f', list(SYSTEMS), CATEGORIES)

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


class TestStatus:
    def test_summarizes_the_active_families(
        self, prepared: Path, store: tuple[SQLiteDatabase, list[str]]
    ) -> None:
        database, _ = store
        published = publication.read(prepared)
        now = datetime.now(UTC)
        publication.publish(database, published, now)
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

    def test_refuses_a_blank_reason(self, voted: SQLiteDatabase) -> None:
        result = invoke(
            'exclude', 'production', 'writing-f', '--vote', '1', '--reason', ''
        )

        assert result.exit_code == 1
        assert result.stderr == 'Give a reason for the exclusion.\n'


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
        ],
        ids=['exclude-none', 'restore-none', 'restore-two', 'votes-two'],
    )
    def test_refuses_anything_but_one_selector_before_connecting(
        self,
        store: tuple[SQLiteDatabase, list[str]],
        args: tuple[str, ...],
        message: str,
    ) -> None:
        result = invoke(*args)

        assert result.exit_code == 1
        assert result.stderr == message
        assert store[1] == []
