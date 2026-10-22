import textwrap
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
            {'route': 'acme/large'},
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
        def test_hashes_the_identity_of_a_system_under_any_name(self) -> None:
            assert make_system().fingerprint == '293ca99b5baeacd7'
            assert make_system(name='x').fingerprint == '293ca99b5baeacd7'
            assert make_system(display_name='x').fingerprint == '293ca99b5baeacd7'

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


class TestLoadSystems:
    def test_resolves_each_table(
        self, export: NixosConfigExport, tmp_path: Path
    ) -> None:
        path = tmp_path / 'systems.toml'
        path.write_text(
            textwrap.dedent(
                """\
                [large]
                model = "acme-large"

                [small]
                model = "acme-small"
                """
            )
        )

        systems = load_systems(export, path)

        assert {name: system.route for name, system in systems.items()} == {
            'large': 'acme/large',
            'small': 'acme/small',
        }

    def test_rejects_two_systems_sharing_a_display_name(
        self, export: NixosConfigExport, tmp_path: Path
    ) -> None:
        path = tmp_path / 'systems.toml'
        path.write_text(
            textwrap.dedent(
                """\
                [large]
                model = "acme-large"

                [large-codex]
                model = "acme-large"
                prompt = "nixos-config:codex"
                """
            )
        )

        with pytest.raises(ValueError) as error:
            load_systems(export, path)

        assert str(error.value) == (
            "systems 'large' and 'large-codex' share the display name 'Acme Large', "
            'so set a distinct `display_name`'
        )

    def test_committed_systems_resolve_against_the_pinned_catalog(self) -> None:
        systems = load_systems(load_export())

        assert {name: system.display_name for name, system in systems.items()} == {
            'opus-5.5': 'Claude Opus 5.5',
            'gpt-6-astra': 'GPT-6 Astra',
            'gpt-6.1-sol': 'GPT-6.1 Sol',
            'gemini-4-argon': 'Gemini 4 Argon',
            'gemini-3.8-flash': 'Gemini 3.8 Flash',
            'deepseek-v4.1-flash-1017': 'DeepSeek V4.1 Flash 1017',
            'kimi-k3': 'Kimi K3',
        }


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
            'x',
            SystemConfig(
                route='router/acme/x', effort='max', price=price, display_name='Acme X'
            ),
            export,
        )

        assert (system.display_name, system.route, system.effort, system.price) == (
            'Acme X',
            'router/acme/x',
            'max',
            price,
        )

    def test_takes_a_display_name_over_the_catalog_name(
        self, export: NixosConfigExport
    ) -> None:
        system = resolve_system(
            'large',
            SystemConfig(model='acme-large', display_name='Acme Large 0901'),
            export,
        )

        assert system.display_name == 'Acme Large 0901'

    def test_reads_a_nixos_config_system_prompt(
        self, export: NixosConfigExport
    ) -> None:
        system = resolve_system(
            'large',
            SystemConfig(model='acme-large', prompt='nixos-config:claudeCode'),
            export,
        )

        assert system.prompt == '# CLAUDE.md\n\nBe direct.'

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
                SystemConfig(model='acme-unrouted'),
                "system 'x': catalog model 'acme-unrouted' has no channel",
            ),
            (
                SystemConfig(model='acme-routed'),
                "system 'x': catalog model 'acme-routed' has channels acme, router, "
                'so set its `route` and `price` instead',
            ),
            (
                SystemConfig(model='acme-large', prompt='nixos-config:omp'),
                "system 'x': nixos-config has no 'omp' instructions "
                '(expected one of claudeCode, codex)',
            ),
            (
                SystemConfig(model='acme-large', prompt='prompts/brief.md'),
                "system 'x': prompt 'prompts/brief.md' is not nixos-config:<client>",
            ),
        ],
    )
    def test_rejects_what_the_catalog_does_not_offer(
        self, export: NixosConfigExport, config: SystemConfig, message: str
    ) -> None:
        with pytest.raises(ValueError) as error:
            resolve_system('x', config, export)

        assert str(error.value) == message
