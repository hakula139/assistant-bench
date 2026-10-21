from pathlib import Path

import pytest
from pydantic import ValidationError

from assistant_bench.catalog import (
    NixosConfigExport,
    Price,
    SystemConfig,
    load_export,
    load_systems,
    resolve_system,
)
from support import make_system


class TestPrice:
    def test_cost_bills_cached_input_at_the_cache_read_rate(self) -> None:
        price = Price(input=2.0, output=10.0, cache_read=0.2)

        cost = price.cost(input_tokens=1_000, cached_tokens=600, output_tokens=500)

        assert cost == pytest.approx((400 * 2.0 + 600 * 0.2 + 500 * 10.0) / 1e6)


class TestLoadExport:
    def test_reads_the_file_named_by_the_dev_shell(
        self, export: NixosConfigExport
    ) -> None:
        loaded = load_export()

        assert loaded == export
        assert loaded.rev == '0123456789abcdef0123456789abcdef01234567'

    def test_names_the_dev_shell_when_the_variable_is_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv('BENCH_NIXOS_CONFIG', raising=False)

        with pytest.raises(RuntimeError, match='inside `nix develop`'):
            load_export()


class TestSystemConfig:
    @pytest.mark.parametrize(
        'table',
        [
            {},
            {'model': 'acme-large', 'route': 'acme/large'},
            {
                'model': 'acme-large',
                'price': {'input': 1, 'output': 1, 'cache_read': 0},
            },
        ],
    )
    def test_rejects_an_ambiguous_source_or_price(
        self, table: dict[str, object]
    ) -> None:
        with pytest.raises(ValidationError):
            SystemConfig.model_validate(table)


class TestSystem:
    class TestFingerprint:
        def test_ignores_the_name(self) -> None:
            renamed = make_system(name='x')

            assert renamed.fingerprint == make_system().fingerprint

        @pytest.mark.parametrize(
            'change',
            [
                {'route': 'acme/large-2'},
                {'effort': 'high'},
                {'prompt': 'Be brief.'},
            ],
        )
        def test_changes_with_what_shapes_the_outputs(
            self, change: dict[str, str]
        ) -> None:
            assert make_system(**change).fingerprint != make_system().fingerprint


class TestResolveSystem:
    def test_applies_the_default_effort_a_model_requires(
        self, export: NixosConfigExport
    ) -> None:
        system = resolve_system('large', SystemConfig(model='acme-large'), export)

        assert system == make_system(name='large')

    def test_leaves_an_optional_effort_to_the_model(
        self, export: NixosConfigExport
    ) -> None:
        system = resolve_system('small', SystemConfig(model='acme-small'), export)

        assert system.route == 'acme/small'
        assert system.effort is None

    def test_keeps_an_accepted_effort(self, export: NixosConfigExport) -> None:
        system = resolve_system(
            'small', SystemConfig(model='acme-small', effort='low'), export
        )

        assert system.effort == 'low'

    def test_reports_a_zero_priced_channel_as_unpriced(
        self, export: NixosConfigExport
    ) -> None:
        system = resolve_system('local', SystemConfig(model='local-model'), export)

        assert system.route == 'local/model'
        assert system.price is None

    def test_takes_a_route_and_price_as_given(self, export: NixosConfigExport) -> None:
        price = Price(input=1.0, output=4.0, cache_read=0.1)

        system = resolve_system(
            'x', SystemConfig(route='router/acme/x', effort='max', price=price), export
        )

        assert (system.route, system.effort, system.price) == (
            'router/acme/x',
            'max',
            price,
        )

    def test_reads_a_nixos_config_system_prompt(
        self, export: NixosConfigExport
    ) -> None:
        system = resolve_system(
            'large',
            SystemConfig(model='acme-large', prompt='nixos-config:claudeCode'),
            export,
        )

        assert system.prompt == '# CLAUDE.md\n\nBe direct.'

    def test_reads_a_repo_relative_system_prompt(
        self, export: NixosConfigExport
    ) -> None:
        path = 'families/smoke/tasks/echo/instruction.md'

        system = resolve_system(
            'large', SystemConfig(model='acme-large', prompt=path), export
        )

        assert system.prompt == (Path(__file__).parents[1] / path).read_text()

    @pytest.mark.parametrize(
        ('config', 'message'),
        [
            (
                SystemConfig(model='acme-huge'),
                "system 'x': unknown catalog model 'acme-huge'",
            ),
            (
                SystemConfig(model='acme-small', effort='medium'),
                "system 'x': effort 'medium' is not one of low, high",
            ),
            (
                SystemConfig(model='local-model', effort='low'),
                "system 'x': the model takes no effort",
            ),
            (
                SystemConfig(model='acme-large', prompt='nixos-config:omp'),
                "system 'x': nixos-config has no 'omp' instructions "
                '(expected one of claudeCode, codex)',
            ),
        ],
    )
    def test_rejects_what_the_catalog_does_not_offer(
        self, export: NixosConfigExport, config: SystemConfig, message: str
    ) -> None:
        with pytest.raises(ValueError) as error:
            resolve_system('x', config, export)

        assert str(error.value) == message


class TestLoadSystems:
    def test_resolves_each_table(
        self, export: NixosConfigExport, tmp_path: Path
    ) -> None:
        path = tmp_path / 'systems.toml'
        path.write_text(
            '[large]\nmodel = "acme-large"\n\n[small]\nmodel = "acme-small"\n'
        )

        systems = load_systems(export, path)

        assert {name: system.route for name, system in systems.items()} == {
            'large': 'acme/large',
            'small': 'acme/small',
        }

    def test_committed_systems_resolve_against_the_pinned_catalog(self) -> None:
        systems = load_systems(load_export())

        assert set(systems) == {
            'opus-5.5',
            'gpt-6.1-sol',
            'gemini-4-argon',
            'gemini-3.8-flash',
            'deepseek-v4.1-flash',
        }
