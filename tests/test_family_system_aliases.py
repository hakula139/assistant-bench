import asyncio
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock

import pytest
from harbor.job_diff import DiffAction, compare_job_trials, load_source_trials
from harbor.job_plan import JobPlan
from typer.testing import CliRunner

from assistant_bench import cli, jobs
from assistant_bench.catalog import (
    NixosConfigExport,
    SystemAlias,
    load_export,
    load_systems,
)
from assistant_bench.families import case_task, load_family
from assistant_bench.harnesses import agent_kwargs, fingerprint
from assistant_bench.paths import FAMILIES_DIR
from assistant_bench.runtime import RunContext
from support import (
    FIXTURES,
    VOTES,
    break_setting,
    finish_job,
    make_system,
    voted_tasks,
    with_settings,
    write_votes_family,
)


class TestListedSystems:
    def test_instruction_pairs_keep_their_agent_kwargs_and_storage_keys(self) -> None:
        export = load_export()
        models = load_systems(export)
        family = load_family('instructions-format')

        members = jobs.listed_systems(family, models, export=export)
        jobs.check_systems(family, members)

        assert {member.name for member in members} == set(models) | {
            f'{name}-nixos' for name in models
        }
        for member in members:
            prompted = member.name.endswith('-nixos')
            name = member.name.removesuffix('-nixos')
            base = models[name]
            expected = base.model_copy(
                update={
                    'name': member.name,
                    'prompt': export.instructions['claudeCode'] if prompted else None,
                }
            )
            assert agent_kwargs(member) == agent_kwargs(expected)
            assert member.public_id(family.name) == f'instructions-format/{name}'
            assert member.condition == ('prompted' if prompted else 'default')
            assert member.display_name == (
                f'{base.display_name} · Instructions' if prompted else base.display_name
            )

    @pytest.mark.parametrize(
        ('key', 'model', 'api', 'harness', 'version', 'label'),
        [
            (
                'opus-5.5-claude-code-high',
                'opus-5.5-high',
                'anthropic-messages',
                'claude-code',
                '2.1.319',
                'Claude Opus 5.5 · Claude Code · High',
            ),
            (
                'gpt-6.2-sol-codex-high',
                'gpt-6.2-sol-high',
                'openai-responses',
                'codex',
                '0.167.0',
                'GPT-6.2 Sol · Codex · High',
            ),
        ],
    )
    def test_installed_arms_keep_their_transport_and_pinned_agent(
        self, key: str, model: str, api: str, harness: str, version: str, label: str
    ) -> None:
        export = load_export()
        models = load_systems(export)
        family = load_family('coding-repo')
        old = models[model].model_copy(
            update={'name': key, 'api': api, 'harness': harness, 'version': version}
        )

        listed = jobs.listed_systems(family, models, export=export)
        jobs.check_systems(family, listed)
        members = {s.name: s for s in jobs.family_systems(family, listed)}

        assert agent_kwargs(members[key]) == agent_kwargs(old)
        assert members[key].public_id(family.name) == f'coding-repo/{model}'
        assert members[key].condition == 'installed'
        assert members[key].display_name == label
        assert members[model].harness == 'terminus-2'
        assert members[model].public_id(family.name) == members[key].public_id(
            family.name
        )
        assert members[model].condition == 'default'

    def test_refuses_unlisted_system_aliases_key(self, families_dir: Path) -> None:
        cases = write_votes_family(families_dir, ['acme-large'], name='unlisted')
        with cases.with_name('family.toml').open('a') as config:
            config.write(
                '\n[system_aliases.stray]\n'
                'model_effort = "acme-large"\n'
                'condition = "prompted"\n'
                'prompt = "nixos-config:claudeCode"\n'
                'display_name = "Stray"\n'
            )

        with pytest.raises(
            ValueError, match='system aliases must be listed in `systems`: `stray`'
        ):
            load_family('unlisted', root=families_dir)

    def test_refuses_alias_shadowing_canonical_key(
        self, export: NixosConfigExport
    ) -> None:
        base = make_system()
        alias = SystemAlias(
            model_effort=base.name,
            condition='prompted',
            display_name='Shadow',
            prompt='nixos-config:claudeCode',
        )
        family = with_settings(
            VOTES,
            systems=[base.name],
            system_aliases={base.name: alias},
        )

        with pytest.raises(ValueError, match='shadow canonical keys'):
            jobs.listed_systems(family, {base.name: base}, export=export)

    def test_deduplicates_unknown_canonical_keys(
        self, export: NixosConfigExport
    ) -> None:
        alias = SystemAlias(
            model_effort='missing-model',
            condition='prompted',
            display_name='Missing Prompted',
            prompt='nixos-config:claudeCode',
        )
        family = with_settings(
            VOTES,
            systems=['missing-model', 'missing-prompted'],
            system_aliases={'missing-prompted': alias},
        )

        with pytest.raises(ValueError) as error:
            jobs.listed_systems(family, {}, export=export)

        assert str(error.value).startswith(
            'Family `f` lists `missing-model`, which `systems.toml` does not define.'
        )

    def test_refuses_duplicate_display_name(self, export: NixosConfigExport) -> None:
        base = make_system()
        alias = SystemAlias(
            model_effort=base.name,
            condition='prompted',
            display_name=base.display_name,
            prompt='nixos-config:claudeCode',
        )
        family = with_settings(
            VOTES,
            systems=[base.name, 'prompted'],
            system_aliases={'prompted': alias},
        )

        with pytest.raises(
            ValueError, match=f'repeats the display name `{base.display_name}`'
        ):
            jobs.listed_systems(family, {base.name: base}, export=export)

    def test_refuses_two_aliases_with_the_same_public_id_and_condition(
        self, export: NixosConfigExport
    ) -> None:
        family = load_family('instructions-format')
        base = make_system()
        alias = SystemAlias(
            model_effort=base.name,
            condition='prompted',
            display_name='First',
            prompt='nixos-config:claudeCode',
        )
        family = with_settings(
            family,
            pairs=[],
            systems=['first', 'second'],
            system_aliases={
                'first': alias,
                'second': alias.model_copy(update={'display_name': 'Second'}),
            },
        )

        with pytest.raises(ValueError, match='repeats the `prompted` condition'):
            jobs.listed_systems(family, {base.name: base}, export=export)


class TestResolveFamilySystems:
    def test_allows_rekeying_a_canonical_configuration(
        self, export: NixosConfigExport
    ) -> None:
        base = make_system()
        alias = SystemAlias(
            model_effort=base.name,
            condition='default',
            display_name='Acme Large v2',
        )
        family = with_settings(
            VOTES,
            systems=['acme-large-v2'],
            system_aliases={'acme-large-v2': alias},
        )

        (resolved,) = jobs.resolve_family_systems(family, {base.name: base}, export)

        assert resolved.name == 'acme-large-v2'
        assert resolved.canonical_name == base.name
        assert resolved.condition == 'default'
        assert fingerprint(resolved) == fingerprint(base)

    def test_refuses_duplicate_canonical_fingerprint_bindings(
        self, export: NixosConfigExport
    ) -> None:
        base = make_system()
        alias = SystemAlias(
            model_effort=base.name,
            condition='first',
            display_name='First',
            prompt='nixos-config:claudeCode',
        )
        family = with_settings(
            VOTES,
            systems=['first', 'second'],
            system_aliases={
                'first': alias,
                'second': alias.model_copy(
                    update={'condition': 'second', 'display_name': 'Second'}
                ),
            },
        )

        with pytest.raises(
            ValueError, match='conditions `first` and `second` to the same fingerprint'
        ):
            jobs.resolve_family_systems(family, {base.name: base}, export)


class TestJob:
    def test_harbor_reuses_and_regrades_the_original_installed_agent_trials(
        self, families_dir: Path, data_dir: Path
    ) -> None:
        export = load_export()
        models = load_systems(export)
        family = load_family('coding-repo', root=FAMILIES_DIR)
        fixture = voted_tasks(families_dir, 'keep', 'regrade')
        tasks = [case_task(fixture, task) for task in ('keep', 'regrade')]
        name = 'gpt-6.2-sol-codex-high'
        previous = models['gpt-6.2-sol-high'].model_copy(
            update={
                'name': name,
                'api': 'openai-responses',
                'harness': 'codex',
                'version': '0.167.0',
            }
        )
        archived = finish_job(
            jobs.job(family, [previous], tasks, root=data_dir, sources=[])
        )
        files = {
            path: path.read_bytes() for path in archived.rglob('*') if path.is_file()
        }
        break_setting(tasks[1], ('task', 'version'), '1.1.0')
        selected = [
            system
            for system in jobs.listed_systems(family, models, export=export)
            if system.public_id(family.name) == 'coding-repo/gpt-6.2-sol-high'
            and system.condition == 'installed'
        ]
        sources = [jobs.job_source(archived)]
        current = jobs.job(family, selected, tasks, root=data_dir, sources=sources)

        plan = asyncio.run(JobPlan.from_config(current))
        diff = compare_job_trials(plan, load_source_trials(sources))

        assert {trial.task_name: trial.action for trial in diff.trials} == {
            'f/keep': DiffAction.REUSE,
            'f/regrade': DiffAction.REGRADE,
        }
        assert {
            path: path.read_bytes() for path in archived.rglob('*') if path.is_file()
        } == files


class TestFingerprints:
    @pytest.mark.parametrize(
        'arguments',
        [
            ['fingerprints'],
            [
                'spend',
                '--system',
                'instructions-format/gpt-6.1-sol-high',
                '--condition',
                'prompted',
            ],
        ],
        ids=['manifest-guard', 'selected-run-guard'],
    )
    def test_checks_the_frozen_storage_key_of_a_prompted_alias(
        self, arguments: list[str], reports_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        export = load_export()
        systems = load_systems(export)
        name = 'gpt-6.1-sol-high'
        archived = (FIXTURES / 'report-checkers.json').read_text()
        archived = (
            archived.replace('"acme"', f'"{name}"')
            .replace('"acme-prompted"', f'"{name}-nixos"')
            .replace('"acme-hash"', f'"{fingerprint(systems[name])}"')
        )
        path = reports_dir / 'instructions-format' / '2026-10-21.json'
        path.parent.mkdir(parents=True)
        path.write_bytes(archived.encode())
        public = reports_dir.parent
        (public / 'families').symlink_to(FAMILIES_DIR)
        context = Mock(
            spec=RunContext, public_root=public, private_root=public / 'private'
        )
        context.sources.return_value = []
        monkeypatch.setattr(cli, 'open_context', lambda: nullcontext(context))
        monkeypatch.setattr(cli, '_catalog', Mock(return_value=(export, systems)))

        result = CliRunner().invoke(cli.app, arguments)

        assert result.exit_code == 1, result.output
        assert 'acme-prompted-hash' in result.stderr
        assert path.read_bytes() == archived.encode()
