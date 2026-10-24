import textwrap
from importlib import metadata
from pathlib import Path

import pytest
from pydantic import ValidationError

from assistant_bench.catalog import (
    NixosConfigExport,
    Price,
    System,
    SystemConfig,
    load_export,
    load_systems,
    resolve_system,
)
from assistant_bench.generation import Generation
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

    @pytest.mark.parametrize('field', ['maxTokens', 'contextWindow'])
    def test_requires_catalog_capacity_metadata(
        self, export: NixosConfigExport, field: str
    ) -> None:
        document = export.model_dump(by_alias=True)
        document['catalog']['models']['acme-large'].pop(field)

        with pytest.raises(ValidationError, match=field):
            NixosConfigExport.model_validate(document)


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
            {'model': 'acme-large', 'max_output_tokens': 64},
            {'model': 'acme-large', 'context_window': 8192},
            {
                'route': 'acme/large',
                'display_name': 'Acme Large',
                'max_output_tokens': 64,
            },
            {
                'route': 'acme/large',
                'display_name': 'Acme Large',
                'context_window': 8192,
            },
        ],
        ids=[
            'no-source',
            'both-sources',
            'route-without-display-name',
            'model-with-price',
            'model-with-vendor',
            'model-with-output-override',
            'model-with-context-override',
            'route-without-context',
            'route-without-output-capacity',
        ],
    )
    def test_rejects_an_ambiguous_or_incomplete_table(
        self, table: dict[str, object]
    ) -> None:
        with pytest.raises(ValidationError):
            SystemConfig.model_validate(table)


class TestSystem:
    @pytest.mark.parametrize(
        ('limit', 'value'),
        [
            pytest.param('max_output_tokens', 0, id='zero-output'),
            pytest.param('max_output_tokens', -1, id='negative-output'),
            pytest.param('context_window', 0, id='zero-context'),
            pytest.param('context_window', -1, id='negative-context'),
            pytest.param('max_output_tokens', 8192, id='no-prompt-room'),
            pytest.param('max_output_tokens', 8193, id='output-exceeds-context'),
        ],
    )
    def test_rejects_a_capacity_that_cannot_leave_room_for_a_prompt(
        self, limit: str, value: int
    ) -> None:
        fields = make_system().model_dump() | {limit: value}

        with pytest.raises(ValidationError):
            System.model_validate(fields)

    class TestFingerprint:
        def test_captured_identity_stays_stable_when_the_runtime_changes(
            self, monkeypatch: pytest.MonkeyPatch
        ) -> None:
            generate = make_system()
            terminal = make_system(harness='terminus-2')
            before = (generate.fingerprint, terminal.fingerprint)

            def version(name: str) -> str:
                return '999.0.0'

            monkeypatch.setattr(metadata, 'version', version)

            after = (generate.fingerprint, terminal.fingerprint)

            assert after == before

        @pytest.mark.parametrize(
            ('changed', 'expected'),
            [
                pytest.param(
                    Generation(protocol=2, harbor='0.24.0'), (True, True), id='protocol'
                ),
                pytest.param(
                    Generation(protocol=1, harbor='0.25.0'), (False, True), id='harbor'
                ),
            ],
        )
        def test_versions_invalidate_only_the_outputs_they_shape(
            self, changed: Generation, expected: tuple[bool, bool]
        ) -> None:
            base = Generation(protocol=1, harbor='0.24.0')

            differences = tuple(
                make_system(harness=harness, generation=base).fingerprint
                != make_system(harness=harness, generation=changed).fingerprint
                for harness in ('generate', 'terminus-2')
            )

            assert differences == expected

        def test_hashes_the_identity_of_a_system_under_any_name(self) -> None:
            assert make_system().fingerprint == '0612e0518a555f77'
            assert make_system(name='x').fingerprint == '0612e0518a555f77'
            assert make_system(display_name='x').fingerprint == '0612e0518a555f77'
            assert make_system(vendor='other').fingerprint == '0612e0518a555f77'

        @pytest.mark.parametrize(
            'change',
            [
                {'route': 'acme/large-2'},
                {'effort': 'high'},
                {'prompt': 'Be brief.'},
                {'harness': 'terminus-2'},
                {'max_output_tokens': 128},
                {'context_window': 16384},
            ],
            ids=['route', 'effort', 'prompt', 'harness', 'output-capacity', 'context'],
        )
        def test_changes_with_what_shapes_the_outputs(
            self, change: dict[str, str | int]
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
            'opus-5.5-max': 'Claude Opus 5.5 · Max',
            'gpt-6-astra-max': 'GPT-6 Astra · Max',
            'gpt-6.1-sol-max': 'GPT-6.1 Sol · Max',
            'gemini-4-argon-high': 'Gemini 4 Argon · High',
            'gemini-3.8-flash-high': 'Gemini 3.8 Flash · High',
            'deepseek-v4.1-flash-1017-max': 'DeepSeek V4.1 Flash 1017 · Max',
            'kimi-k3-max': 'Kimi K3 · Max',
        }
        assert {
            name: system.effort
            for name, system in systems.items()
            if name != 'opus-5.5'
        } == {
            'opus-5.5-max': 'max',
            'gpt-6-astra-max': 'max',
            'gpt-6.1-sol-max': 'max',
            'gemini-4-argon-high': 'high',
            'gemini-3.8-flash-high': 'high',
            'deepseek-v4.1-flash-1017-max': 'max',
            'kimi-k3-max': 'max',
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
        assert (system.max_output_tokens, system.context_window) == (32, 2048)

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
                max_output_tokens=32768,
                context_window=131072,
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
        assert (system.max_output_tokens, system.context_window) == (32768, 131072)

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
