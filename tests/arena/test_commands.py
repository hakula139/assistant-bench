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
        assert 'with 2 cases and 4 outputs. 1 counted votes from 1 voters' in (
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
