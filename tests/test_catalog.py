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
    pin,
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
            {'model': 'acme-large', 'vendor': 'other'},
        ],
        ids=[
            'no-source',
            'both-sources',
            'route-without-display-name',
            'model-with-price',
            'model-with-vendor',
        ],
    )
    def test_rejects_an_ambiguous_or_incomplete_table(
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
            assert make_system(vendor='other').fingerprint == '293ca99b5baeacd7'

        @pytest.mark.parametrize(
            'change',
            [
                {'route': 'acme/large-2'},
                {'effort': 'high'},
                {'prompt': 'Be brief.'},
                {'harness': 'terminus-2'},
            ],
            ids=['route', 'effort', 'prompt', 'harness'],
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
            'Systems `large` and `large-codex` share the display name `Acme Large`. '
            'Set a distinct `display_name`.'
        )

    @pytest.mark.parametrize(
        ('document', 'message'),
        [
            pytest.param(
                """\
                [large]
                model = "acme-large"

                [broken]
                effort = "low"
                """,
                '`systems.toml`: `broken`: set exactly one of `model` and `route`.',
                id='invalid-table',
            ),
            pytest.param(
                """\
                [large]
                effort = "low"
                route = 5
                display_name = "Large"
                """,
                '`systems.toml`: `large.route`: Input should be a valid string.',
                id='invalid-field',
            ),
            pytest.param(
                '[large',
                '`systems.toml` is not valid TOML: ',
                id='malformed-toml',
            ),
        ],
    )
    def test_names_the_file_and_field_that_fail_to_validate(
        self, export: NixosConfigExport, tmp_path: Path, document: str, message: str
    ) -> None:
        path = tmp_path / 'systems.toml'
        path.write_text(textwrap.dedent(document))

        with pytest.raises(ValueError) as error:
            load_systems(export, path)

        assert str(error.value).startswith(message)

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
    def test_resolves_historical_exports_without_vendor_metadata(
        self, export: NixosConfigExport
    ) -> None:
        document = export.model_dump(by_alias=True)
        for model in document['catalog']['models'].values():
            model.pop('vendor')
        historical = NixosConfigExport.model_validate(document)

        system = resolve_system('large', SystemConfig(model='acme-large'), historical)

        assert system.vendor is None
        assert system.fingerprint == make_system().fingerprint

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
        assert system.vendor == 'other'
        assert system.price is None

    def test_takes_a_route_and_price_as_given(self, export: NixosConfigExport) -> None:
        price = Price(input=1.0, output=4.0, cache_read=0.1)

        system = resolve_system(
            'x',
            SystemConfig(
                route='router/acme/x',
                vendor='acme',
                effort='max',
                price=price,
                display_name='Acme X',
            ),
            export,
        )

        assert (system.display_name, system.route, system.effort, system.price) == (
            'Acme X',
            'router/acme/x',
            'max',
            price,
        )
        assert system.vendor == 'acme'

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
                'System `x`: model `acme-huge` is not in the nixos-config catalog, '
                '`data/llm-models.nix`. Check it against `acme-large`, '
                '`acme-routed`, `acme-unrouted`.',
            ),
            (
                SystemConfig(model='zeta'),
                'System `x`: model `zeta` is not in the nixos-config catalog, '
                '`data/llm-models.nix`.',
            ),
            (
                SystemConfig(model='acme-small', effort='medium'),
                'System `x`: effort `medium` is not one of `low`, `high`.',
            ),
            (
                SystemConfig(model='local-model', effort='low'),
                'System `x`: the model takes no effort.',
            ),
            (
                SystemConfig(model='acme-unrouted'),
                'System `x`: catalog model `acme-unrouted` has no channel.',
            ),
            (
                SystemConfig(model='acme-routed'),
                'System `x`: catalog model `acme-routed` has channels `acme`, '
                '`router`. Set its `route` and `price` instead.',
            ),
            (
                SystemConfig(model='acme-large', prompt='nixos-config:omp'),
                'System `x`: nixos-config has no `omp` instructions. Choose among '
                '`claudeCode`, `codex`.',
            ),
            (
                SystemConfig(model='acme-large', prompt='prompts/brief.md'),
                'System `x`: prompt `prompts/brief.md` is not `nixos-config:<client>`.',
            ),
        ],
        ids=[
            'unknown-model-near-a-key',
            'unknown-model',
            'unoffered-effort',
            'effort-for-a-model-without-one',
            'no-channel',
            'several-channels',
            'unknown-prompt-client',
            'prompt-outside-nixos-config',
        ],
    )
    def test_rejects_what_the_catalog_does_not_offer(
        self, export: NixosConfigExport, config: SystemConfig, message: str
    ) -> None:
        with pytest.raises(ValueError) as error:
            resolve_system('x', config, export)

        assert str(error.value) == message


class TestPin:
    def test_keeps_vendor_metadata_without_changing_the_request(
        self, export: NixosConfigExport
    ) -> None:
        system = make_system()

        pinned = resolve_system(system.name, pin(system), export)

        assert pinned == system
        assert pinned.vendor == 'acme'
        assert pinned.fingerprint == system.fingerprint
