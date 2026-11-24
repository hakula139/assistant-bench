import json
import textwrap
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock

import pytest
from rich.text import Text
from typer.testing import CliRunner

from assistant_bench import canaries, cli
from assistant_bench.catalog import NixosConfigExport
from assistant_bench.database import SQLiteDatabase
from assistant_bench.harnesses import fingerprint
from assistant_bench.harnesses.usage import MeasuredUsage
from assistant_bench.runtime import RunContext
from assistant_bench.spend import Accounting, Entry
from assistant_bench.state import Run, State
from support import START, make_system, write_votes_family


@pytest.fixture
def family_catalog(
    families_dir: Path, export: NixosConfigExport, monkeypatch: pytest.MonkeyPatch
) -> None:
    cases = write_votes_family(
        families_dir,
        ['acme-large-medium', 'acme-large-prompted'],
        {'example': 'message'},
        name='comparison',
    )
    with cases.with_name('family.toml').open('a') as config:
        config.write(
            textwrap.dedent(
                """\
                [system_aliases.acme-large-prompted]
                model_effort = "acme-large-medium"
                condition = "prompted"
                prompt = "nixos-config:claudeCode"
                display_name = "Acme Large with Instructions"
                """
            )
        )
    write_votes_family(families_dir, ['acme-large-medium'], name='writing')
    base = make_system(name='acme-large-medium')

    monkeypatch.setattr(cli, '_catalog', Mock(return_value=(export, {base.name: base})))


class TestSystems:
    @pytest.mark.usefixtures('family_catalog')
    def test_prints_public_ids_with_conditions_and_display_names(self) -> None:
        result = CliRunner().invoke(cli.app, ['systems', 'comparison'])

        assert result.exit_code == 0, result.output
        assert result.stdout.splitlines() == [
            'comparison/acme-large-medium\tdefault\tAcme Large',
            'comparison/acme-large-medium\tprompted\tAcme Large with Instructions',
        ]


@pytest.mark.usefixtures('family_catalog')
class TestRunTargets:
    @pytest.fixture
    def prepared(self, families_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Mock:
        context = Mock(
            spec=RunContext, public_root=families_dir.parent, root=families_dir.parent
        )
        context.reconcile_publications.return_value = []
        monkeypatch.setattr(cli, 'open_context', lambda: nullcontext(context))
        monkeypatch.setattr(canaries, 'prepare', Mock(return_value=[]))
        prepare = Mock(return_value=[])
        monkeypatch.setattr(cli, '_prepare', prepare)
        return prepare

    @pytest.mark.parametrize(
        ('command', 'selectors', 'expected'),
        [
            pytest.param(
                'run',
                ['--system', 'comparison/acme-large-medium'],
                [('comparison', ['acme-large-medium', 'acme-large-prompted'])],
                id='qualified-system-selects-both-conditions-in-one-family',
            ),
            pytest.param(
                'run',
                ['--system', 'acme-large-medium', '--condition', 'default'],
                [
                    ('comparison', ['acme-large-medium']),
                    ('writing', ['acme-large-medium']),
                ],
                id='bare-system-selects-default-condition-across-families',
            ),
            pytest.param(
                'spend',
                [
                    'comparison',
                    '--system',
                    'comparison/acme-large-medium',
                    '--condition',
                    'prompted',
                ],
                [('comparison', ['acme-large-prompted'])],
                id='spend-selects-requested-condition',
            ),
            pytest.param(
                'run',
                ['comparison', '--condition', 'prompted'],
                [('comparison', ['acme-large-prompted'])],
                id='run-family-condition-without-system',
            ),
            pytest.param(
                'spend',
                ['comparison', '--condition', 'prompted'],
                [('comparison', ['acme-large-prompted'])],
                id='spend-family-condition-without-system',
            ),
        ],
    )
    def test_selects_the_requested_bindings(
        self,
        prepared: Mock,
        command: str,
        selectors: list[str],
        expected: list[tuple[str, list[str]]],
    ) -> None:
        result = CliRunner().invoke(cli.app, [command, *selectors])

        assert result.exit_code == 0, result.output
        prepared.assert_called_once()
        targets = prepared.call_args.args[0]
        assert [
            (target.family.name, [system.name for system in target.selected])
            for target in targets
        ] == expected

    @pytest.mark.parametrize(
        ('selectors', 'message'),
        [
            (
                ['--system', 'comparison/acme-large-prompted'],
                'Family `comparison` does not list `comparison/acme-large-prompted`.',
            ),
            (
                ['writing', '--system', 'comparison/acme-large-medium'],
                'No selected family lists `comparison/acme-large-medium`.',
            ),
            (
                ['--system', 'writing/acme-large-medium', '--condition', 'prompted'],
                'No system with condition `prompted` matches '
                '`writing/acme-large-medium`.',
            ),
            (
                ['writing', '--condition', 'prompted'],
                'Family `writing` has no system with condition `prompted`.',
            ),
            (
                ['--condition', 'nonexistent'],
                'No family lists a system with condition `nonexistent`.',
            ),
            (
                [
                    'comparison',
                    'writing',
                    '--system',
                    'acme-large-medium',
                    '--condition',
                    'prompted',
                ],
                'Family `writing` has no system with condition `prompted` matching '
                '`acme-large-medium`.',
            ),
        ],
    )
    def test_refuses_unknown_or_out_of_scope_bindings(
        self, prepared: Mock, selectors: list[str], message: str
    ) -> None:
        result = CliRunner().invoke(
            cli.app, ['run', *selectors], env={'COLUMNS': '200'}
        )

        assert result.exit_code == 2, result.output
        assert message in Text.from_ansi(result.stderr).plain
        prepared.assert_not_called()


class TestSpend:
    @pytest.mark.usefixtures('family_catalog')
    def test_projects_a_pending_alias_under_its_public_id(
        self, run_context: RunContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cli, 'open_context', lambda: nullcontext(run_context))

        result = CliRunner().invoke(
            cli.app,
            [
                'spend',
                '--system',
                'comparison/acme-large-medium',
                '--condition',
                'prompted',
            ],
        )

        assert result.exit_code == 0, result.output
        assert result.stdout.splitlines() == [
            'System `comparison/acme-large-medium` (condition `prompted`): '
            '1 pending trial with no usage to estimate a cost from.',
            'Family `comparison`: $0.00 spent and projected.',
        ]

    @pytest.mark.usefixtures('family_catalog')
    def test_formats_budget_problem_with_public_id_and_condition(
        self,
        families_dir: Path,
        private_store: SQLiteDatabase,
        run_context: RunContext,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config = families_dir / 'comparison' / 'family.toml'
        config.write_text('budget_usd = 1\n' + config.read_text())
        system = make_system(name='acme-large-prompted')
        accounting = Accounting.of(system)
        entry = Entry(
            trial='c__1',
            system=system.name,
            fingerprint='fp',
            started_at=START,
            finished_at=START,
            responses=[MeasuredUsage(1_000_000, 0, 0, 100_000, None)],
            job='job-1',
            price=accounting.price,
            ceiling_usd=accounting.ceiling_usd,
        )
        private_store.execute(
            'INSERT INTO attempt_entries '
            '(scope, job, trial, started_at, claim_id, attempt_id, entry_json) '
            'VALUES (?, ?, ?, ?, NULL, NULL, ?)',
            (
                'comparison',
                entry.job,
                entry.trial,
                entry.started_at.isoformat(),
                entry.model_dump_json(),
            ),
        )
        monkeypatch.setattr(cli, 'open_context', lambda: nullcontext(run_context))

        result = CliRunner().invoke(
            cli.app,
            [
                'spend',
                '--system',
                'comparison/acme-large-medium',
                '--condition',
                'prompted',
            ],
        )

        assert result.exit_code == 0, result.output
        assert (
            'over its `budget_usd` of $1.00: `comparison/acme-large-medium` '
            '(condition `prompted`)'
        ) in result.stdout


class TestResetCanary:
    @pytest.mark.usefixtures('family_catalog')
    def test_clears_only_the_selected_source_of_a_qualified_system(
        self,
        private_store: SQLiteDatabase,
        private_state: State,
        export: NixosConfigExport,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        records: dict[tuple[str, str], tuple[str, bool]] = {}
        for run_id, system in (
            ('default', make_system(name='acme-large-medium')),
            (
                'current',
                make_system(
                    name='acme-large-prompted', prompt=export.instructions['claudeCode']
                ),
            ),
            (
                'other',
                make_system(name='acme-large-prompted', prompt='Another instruction.'),
            ),
        ):
            private_state.create_run(
                Run(
                    id=run_id,
                    scope='canaries',
                    job=run_id,
                    code_revision=None,
                    case_revision=None,
                    created_at=START.isoformat(),
                )
            )
            source = fingerprint(system)
            record = json.dumps(
                {'system': system.name, 'source_generate_fingerprint': source}
            )
            context_key = json.dumps([source, run_id])
            private_state.append_canary(run_id, record)
            assert private_state.establish_baseline(system.name, context_key, run_id)
            records[system.name, context_key] = (record, run_id == 'current')
        monkeypatch.setattr(
            cli, 'connect', Mock(return_value=nullcontext(private_store))
        )

        result = CliRunner().invoke(
            cli.app,
            ['reset-canary', 'comparison/acme-large-medium', '--condition', 'prompted'],
        )

        assert result.exit_code == 0, result.output
        assert result.stdout == (
            'System `comparison/acme-large-medium` (condition `prompted`): '
            'cleared 1 canary baseline context.\n'
        )
        for (key, context), (record, cleared) in records.items():
            assert private_state.baseline(key, context) == (None if cleared else record)
            assert record in private_state.records(key)

    @pytest.mark.usefixtures('family_catalog')
    def test_refuses_condition_with_bare_storage_key(self) -> None:
        result = CliRunner().invoke(
            cli.app,
            ['reset-canary', 'acme-large-medium', '--condition', 'prompted'],
            env={'COLUMNS': '200'},
        )

        assert result.exit_code == 2, result.output
        assert (
            'Specify a family/model-effort ID with this option.'
            in Text.from_ansi(result.stderr).plain
        )

    @pytest.mark.usefixtures('family_catalog')
    def test_reports_system_argument_hint_for_invalid_qualified_id(self) -> None:
        result = CliRunner().invoke(
            cli.app,
            ['reset-canary', 'comparison/acme-large-prompted'],
            env={'COLUMNS': '200'},
        )

        assert result.exit_code == 2, result.output
        stderr = Text.from_ansi(result.stderr).plain
        assert "Invalid value for 'SYSTEM':" in stderr
        assert (
            'Family `comparison` does not list `comparison/acme-large-prompted`.'
            in stderr
        )
