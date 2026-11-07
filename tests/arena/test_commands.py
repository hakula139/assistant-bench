from contextlib import AbstractContextManager, nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from assistant_bench import cli, runtime
from assistant_bench.arena import commands, d1, publication
from assistant_bench.database import D1Error, SQLiteDatabase
from assistant_bench.families import (
    Family,
    load_cases,
    load_family,
)
from assistant_bench.outputs import Output, write_outputs
from assistant_bench.runtime import RunContext
from assistant_bench.state import State
from support import (
    PrivateRepository,
    commit_all,
    git,
    make_system,
    make_trial,
    new_repository,
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
def store(
    private_store: SQLiteDatabase, monkeypatch: pytest.MonkeyPatch
) -> tuple[SQLiteDatabase, list[str]]:
    targets: list[str] = []

    def connect(target: str) -> AbstractContextManager[SQLiteDatabase]:
        targets.append(target)
        return nullcontext(private_store)

    monkeypatch.setattr(d1, 'connect', connect)
    return private_store, targets


@pytest.fixture
def prepared(family: Family, store: tuple[SQLiteDatabase, list[str]]) -> str:
    database, _ = store
    draft = publication.prepare(
        family,
        load_cases(family),
        collected(),
        {name: system.display_name for name, system in SYSTEMS.items()},
        target='production',
    )
    publication.stage(database, draft, NOW)
    database.execute(
        'INSERT INTO collections (id, family, expected_count, manifest_digest, '
        'created_at, sealed) VALUES (?, ?, 0, ?, ?, 1)',
        ['collection', family.name, 'unused', NOW.isoformat()],
    )
    publication.record_preparation(
        database,
        publication.Preparation(
            publication_id=draft.id,
            target='production',
            collection_id='collection',
            case_revision=None,
            prepared_at=NOW,
        ),
    )
    return draft.id


@pytest.fixture
def voted(prepared: str, store: tuple[SQLiteDatabase, list[str]]) -> SQLiteDatabase:
    database, _ = store
    published = publication.read_prepared(database, prepared)
    publication.activate(database, prepared, NOW - timedelta(days=1))
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
def context(
    family: Family,
    families_dir: Path,
    private_repository: PrivateRepository,
    store: tuple[SQLiteDatabase, list[str]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> RunContext:
    public = tmp_path / 'public'
    code_revision = new_repository(
        public,
        {
            f'families/{family.name}/{path.name}': path.read_text()
            for path in (families_dir / family.name).iterdir()
        },
    )
    root = tmp_path / 'execution'
    private = private_repository.client.source(
        private_repository.revision, root / 'private'
    )
    result = RunContext(
        root=root,
        private_root=private / 'cases',
        case_revision=private_repository.revision,
        code_revision=code_revision,
        state=State(store[0]),
        artifacts=private_repository.client,
    )
    monkeypatch.setattr(runtime, 'REPO_ROOT', public)
    monkeypatch.setattr(commands, 'open_context', lambda: nullcontext(result))
    return result


def collect(
    context: RunContext, outputs: list[Output], *, case_revision: str | None = None
) -> str:
    return write_outputs(
        'writing-f',
        outputs,
        context.state,
        previous=context.state.output_generation('writing-f').id,
        code_revision=context.code_revision,
        case_revision=case_revision or context.case_revision,
        system_names={name: system.display_name for name, system in SYSTEMS.items()},
    )


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
    def test_prepares_from_pinned_sources_and_inspects_from_cloud(
        self,
        context: RunContext,
        families_dir: Path,
        store: tuple[SQLiteDatabase, list[str]],
    ) -> None:
        generation = collect(context, collected())
        current = families_dir / 'writing-f' / 'family.toml'
        current.write_text(
            current.read_text().replace('public_voting = true', 'public_voting = false')
        )

        result = invoke('prepare', 'writing-f', 'production')

        assert result.exit_code == 0, result.output
        database, _ = store
        metadata = database.execute(
            'SELECT publication_id FROM publication_preparations'
        ).rows
        identity = str(metadata[0]['publication_id'])
        pinned = publication.preparation(database, identity)
        assert pinned is not None and pinned.collection_id == generation
        assert database.execute('SELECT * FROM active').rows == []
        listing = invoke('inspect', identity)
        assert listing.exit_code == 0, listing.output
        assert 'Public Large' in listing.stdout
        assert 'large on first' in listing.stdout

    def test_reuses_original_preparation_and_votes_after_equivalent_recollection(
        self,
        context: RunContext,
        store: tuple[SQLiteDatabase, list[str]],
        private_repository: PrivateRepository,
    ) -> None:
        original = collect(context, collected())
        assert invoke('prepare', 'writing-f', 'production').exit_code == 0
        database, _ = store
        identity = str(
            database.execute(
                'SELECT publication_id FROM publication_preparations'
            ).rows[0]['publication_id']
        )
        pinned = publication.preparation(database, identity)
        published = publication.read_prepared(database, identity)
        publication.activate(database, identity, NOW)
        identifier = record_vote(
            database, published, NOW, actor='reader', age=timedelta(hours=1)
        )
        (private_repository.seed / 'unrelated.txt').write_text(
            'A new unrelated source revision.\n'
        )
        revision = commit_all(private_repository.seed, 'Update unrelated source')
        git(private_repository.seed, 'push', '--quiet', 'origin', 'main')
        fresh = collect(
            context,
            [*collected(), make_trial('first', 'unlisted', cost_usd=99.0)],
            case_revision=revision,
        )

        result = invoke('prepare', 'writing-f', 'production')

        assert result.exit_code == 0, result.output
        assert 'Reused prepared publication' in result.stdout
        assert fresh != original
        assert publication.preparation(database, identity) == pinned
        assert pinned is not None and pinned.collection_id == original
        assert database.execute('SELECT id FROM votes').rows == [{'id': identifier}]
        assert database.execute('SELECT publication_id FROM active').rows == [
            {'publication_id': identity}
        ]

    @pytest.mark.parametrize(
        'field',
        [
            'cost_usd',
            'input_tokens',
            'output_tokens',
            'cached_tokens',
            'cache_write_tokens',
            'reasoning_tokens',
            'latency_sec',
            'reward',
        ],
    )
    def test_refuses_changed_metrics_with_the_same_publication_identity(
        self, context: RunContext, store: tuple[SQLiteDatabase, list[str]], field: str
    ) -> None:
        collect(context, collected())
        assert invoke('prepare', 'writing-f', 'production').exit_code == 0
        database, _ = store
        identity = str(
            database.execute(
                'SELECT publication_id FROM publication_preparations'
            ).rows[0]['publication_id']
        )
        pinned = publication.preparation(database, identity)
        outputs = collected()
        outputs[0] = outputs[0].model_copy(update={field: 77})
        collect(context, outputs)

        result = invoke('prepare', 'writing-f', 'production')

        assert result.exit_code == 1, result.output
        assert 'whose output facts differ' in result.stderr
        assert publication.preparation(database, identity) == pinned


class TestPublish:
    def test_activates_the_reviewed_cloud_id_once_and_reports_a_repeat(
        self, prepared: str, store: tuple[SQLiteDatabase, list[str]]
    ) -> None:
        database, targets = store

        first = invoke('publish', 'staging', prepared)
        second = invoke('publish', 'staging', prepared)

        assert first.exit_code == second.exit_code == 0, first.output
        assert targets == ['private', 'staging', 'private', 'staging']
        assert (
            f'Published `writing-f` to `staging`: publication `{prepared}`'
            in first.stdout
        )
        assert 'already active' in second.stdout
        assert database.execute('SELECT publication_id FROM active').rows == [
            {'publication_id': prepared}
        ]

    def test_refuses_corrupted_cloud_content_before_destination_connection(
        self, prepared: str, store: tuple[SQLiteDatabase, list[str]]
    ) -> None:
        database, targets = store
        database.execute('UPDATE publications SET display_name = ?', ['Edited'])

        result = invoke('publish', 'production', prepared)

        assert result.exit_code == 1
        assert 'does not match its immutable snapshot' in result.stderr
        assert targets == ['private']
        assert database.execute('SELECT * FROM active').rows == []

    def test_refuses_an_id_without_verified_preparation(
        self, family: Family, store: tuple[SQLiteDatabase, list[str]]
    ) -> None:
        result = invoke('publish', 'production', 'absent')

        assert result.exit_code == 1
        assert 'has no verified preparation' in result.stderr
        assert store[1] == ['private']

    def test_rejects_an_unknown_target_by_name(self, prepared: str) -> None:
        result = invoke('publish', 'prod', prepared)

        assert result.exit_code == 2
        assert "'staging'" in result.stderr and "'production'" in result.stderr


class TestStatus:
    def test_summarizes_the_active_families(
        self, prepared: str, store: tuple[SQLiteDatabase, list[str]]
    ) -> None:
        database, _ = store
        published = publication.read_prepared(database, prepared)
        now = datetime.now(UTC)
        publication.activate(database, prepared, now)
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

        monkeypatch.setattr(d1, 'connect', refuse)

        result = invoke(*args)

        assert result.exit_code == 1
        assert result.stderr == 'D1 refused the statement with HTTP 403. Forbidden.\n'
