import textwrap
from importlib import metadata
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from assistant_bench.catalog import (
    Api,
    NixosConfigExport,
    Price,
    System,
    SystemConfig,
    load_export,
    load_systems,
    resolve_identity,
    resolve_system,
)
from assistant_bench.generation import Generation
from support import make_system


class TestPrice:
    @pytest.mark.parametrize(
        ('writes', 'rate', 'expected'),
        [(0, None, 0.00592), (100, 2.5, 0.00597), (100, None, None)],
        ids=['cache-read', 'priced-cache-write', 'unknown-cache-write-rate'],
    )
    def test_cost_bills_each_measured_cache_category_at_its_declared_rate(
        self, writes: int, rate: float | None, expected: float | None
    ) -> None:
        price = Price(input=2.0, output=10.0, cache_read=0.2, cache_write=rate)

        cost = price.cost(
            input_tokens=1_000,
            cached_tokens=600,
            cache_write_tokens=writes,
            output_tokens=500,
        )

        assert cost == (pytest.approx(expected) if expected is not None else None)


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
            {'model': 'acme-large', 'api': 'unsupported'},
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
            'unsupported-api',
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
        @pytest.mark.parametrize(
            'change',
            [
                {'messages_protocol': 2},
                {'anthropic_api_version': '2026-10-21'},
                {'anthropic': '2.4.0'},
            ],
            ids=['messages-protocol', 'api-version', 'sdk-version'],
        )
        @pytest.mark.parametrize('harness', ['generate', 'terminus-2'])
        def test_native_versions_invalidate_only_messages_outputs(
            self, change: dict[str, Any], harness: str
        ) -> None:
            versions = Generation(
                protocol=2,
                harbor='0.24.0',
                messages_protocol=1,
                anthropic_api_version='2023-06-01',
                anthropic='2.3.0',
            )
            changed = versions.model_copy(update=change)
            native = make_system(
                api='anthropic-messages',
                thinking_mode='anthropic-adaptive',
                harness=harness,
                generation=versions,
            )
            other = make_system(harness=harness, generation=versions)

            assert (
                native.model_copy(update={'generation': changed}).fingerprint
                != native.fingerprint
            )
            assert (
                other.model_copy(update={'generation': changed}).fingerprint
                == other.fingerprint
            )

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
            assert make_system().fingerprint == 'c8899f53b1177f65'
            assert make_system(name='x').fingerprint == 'c8899f53b1177f65'
            assert make_system(display_name='x').fingerprint == 'c8899f53b1177f65'
            assert make_system(vendor='other').fingerprint == 'c8899f53b1177f65'

        @pytest.mark.parametrize(
            'change',
            [
                {'route': 'acme/large-2'},
                {'effort': 'high'},
                {'prompt': 'Be brief.'},
                {'harness': 'terminus-2'},
                {'max_output_tokens': 128},
                {'context_window': 16384},
                {'api': 'openai-responses'},
            ],
            ids=[
                'route',
                'effort',
                'prompt',
                'harness',
                'output-capacity',
                'context',
                'api',
            ],
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
            'opus-5.5-high': 'Claude Opus 5.5 · High',
            'gpt-6-astra-high': 'GPT-6 Astra · High',
            'gpt-6.1-sol-high': 'GPT-6.1 Sol · High',
            'gemini-4-argon-high': 'Gemini 4 Argon · High',
            'gemini-3.8-flash-high': 'Gemini 3.8 Flash · High',
            'deepseek-v4.1-flash-1017-high': 'DeepSeek V4.1 Flash 1017 · High',
            'kimi-k3-high': 'Kimi K3 · High',
        }
        assert {system.effort for system in systems.values()} == {'high'}
        assert {
            name for name, system in systems.items() if system.api == 'openai-responses'
        } == {'gpt-6-astra-high', 'gpt-6.1-sol-high'}
        native = systems['opus-5.5-high']
        assert (
            native.api,
            native.thinking_mode,
            native.max_output_tokens,
            native.context_window,
        ) == ('anthropic-messages', 'anthropic-adaptive', 128000, 1000000)


class TestResolveIdentity:
    def test_requires_native_thinking_metadata_for_a_replayable_identity(
        self, export: NixosConfigExport
    ) -> None:
        document = export.model_dump(by_alias=True)
        document['catalog']['models']['acme-large']['thinking']['mode'] = None
        missing_mode = NixosConfigExport.model_validate(document)
        config = SystemConfig(
            model='acme-large', api='anthropic-messages', effort='high'
        )
        versions = Generation(
            protocol=2,
            harbor='0.24.0',
            messages_protocol=1,
            anthropic_api_version='2023-06-01',
            anthropic='2.3.0',
        )

        with pytest.raises(ValueError):
            resolve_identity('native', config, missing_mode, versions)

    @pytest.mark.parametrize(
        ('api', 'effort', 'required', 'expected'),
        [
            ('openai-chat-completions', None, True, '46256f5027af8dc1'),
            ('openai-responses', 'high', True, 'a6f35da264e89395'),
            ('openai-chat-completions', None, False, '476d95dfb9b26e31'),
            ('openai-responses', 'unlisted', True, '6e7b17ad7560852c'),
        ],
        ids=[
            'required-default',
            'explicit-effort',
            'optional-default',
            'identity-without-admission',
        ],
    )
    def test_projects_historical_facts_while_current_execution_stays_strict(
        self,
        export: NixosConfigExport,
        monkeypatch: pytest.MonkeyPatch,
        api: Api,
        effort: str | None,
        required: bool,
        expected: str,
    ) -> None:
        document = export.model_dump(by_alias=True)
        entry = document['catalog']['models']['acme-large']
        entry.pop('apis')
        entry['thinking']['requiresEffort'] = required
        old = NixosConfigExport.model_validate(document)
        config = SystemConfig(model='acme-large', api=api, effort=effort)
        versions = Generation(protocol=1, harbor='0.23.0')

        def unexpected(*args: object, **kwargs: object) -> Any:
            pytest.fail('Historical identity attempted to read current runtime state')

        monkeypatch.setattr('assistant_bench.catalog.load_generation', unexpected)
        monkeypatch.setattr('assistant_bench.catalog.load_export', unexpected)
        monkeypatch.setattr(metadata, 'version', unexpected)

        identity = resolve_identity('large', config, old, versions)

        assert identity.fingerprint == expected
        with pytest.raises(ValueError, match='declares no supported APIs'):
            resolve_system('large', config, old, versions)

    @pytest.mark.parametrize(
        'change', ['price', 'display-name', 'vendor', 'capabilities', 'export-revision']
    )
    def test_excludes_non_generation_metadata_from_historical_identity(
        self, export: NixosConfigExport, change: str
    ) -> None:
        document = export.model_dump(by_alias=True)
        entry = document['catalog']['models']['acme-large']
        config = SystemConfig(model='acme-large')
        versions = Generation(protocol=1, harbor='0.23.0')
        if change == 'price':
            entry['channelCost'] = {}
        elif change == 'display-name':
            config = config.model_copy(update={'display_name': 'Other label'})
        elif change == 'vendor':
            entry['vendor'] = 'other'
        elif change == 'capabilities':
            entry['apis'] = []
            entry['thinking']['efforts'] = []
        else:
            document['rev'] = 'another-revision'
        changed = NixosConfigExport.model_validate(document)

        identity = resolve_identity('large', config, changed, versions)

        assert identity.fingerprint == '46256f5027af8dc1'


class TestResolveSystem:
    @pytest.mark.parametrize(
        ('change', 'message'),
        [
            (
                {'apis': ['openai-chat-completions']},
                'API `anthropic-messages` is not one of',
            ),
            ({'apis': None}, 'declares no supported APIs'),
            ({'thinking': {'efforts': []}}, 'effort `high` is not one of'),
            ({'thinking': {'mode': 'effort'}}, 'requires adaptive thinking metadata'),
        ],
        ids=[
            'unlisted-api',
            'missing-api-contract',
            'missing-effort-contract',
            'missing-adaptive-mode',
        ],
    )
    def test_rejects_native_capabilities_the_catalog_has_not_declared(
        self, export: NixosConfigExport, change: dict[str, Any], message: str
    ) -> None:
        document = export.model_dump(by_alias=True)
        entry = document['catalog']['models']['acme-large']
        for field, value in change.items():
            if field == 'thinking':
                entry[field].update(value)
            else:
                entry[field] = value
        configured = NixosConfigExport.model_validate(document)
        config = SystemConfig(
            model='acme-large', api='anthropic-messages', effort='high'
        )

        with pytest.raises(ValueError, match=message):
            resolve_system('native', config, configured)

    def test_resolves_native_capacities_and_thinking_from_catalog_metadata(
        self, export: NixosConfigExport
    ) -> None:
        config = SystemConfig(
            model='acme-large', api='anthropic-messages', effort='high'
        )

        resolved = resolve_system('native', config, export)

        assert resolved == make_system(
            name='native',
            api='anthropic-messages',
            effort='high',
            thinking_mode='anthropic-adaptive',
        )

    @pytest.mark.parametrize(
        ('effort', 'default', 'required', 'expected'),
        [
            ('high', 'medium', True, 'high'),
            ('max', 'medium', True, 'invalid'),
            (None, 'high', True, 'high'),
            (None, 'max', True, 'invalid'),
            (None, 'max', False, None),
        ],
        ids=[
            'explicit-effort',
            'unoffered-effort',
            'required-default',
            'unoffered-required-default',
            'optional-omission',
        ],
    )
    def test_validates_effort_against_the_shared_model_list(
        self,
        export: NixosConfigExport,
        effort: str | None,
        default: str,
        required: bool,
        expected: str | None,
    ) -> None:
        document = export.model_dump(by_alias=True)
        thinking = document['catalog']['models']['acme-large']['thinking']
        thinking['defaultLevel'] = default
        thinking['requiresEffort'] = required
        resolved_export = NixosConfigExport.model_validate(document)
        config = SystemConfig(model='acme-large', api='openai-responses', effort=effort)

        if expected == 'invalid':
            with pytest.raises(
                ValueError, match='effort `max` is not one of `low`, `medium`, `high`'
            ):
                resolve_system('large', config, resolved_export)
        else:
            system = resolve_system('large', config, resolved_export)

            assert system.api == config.api
            assert system.effort == expected

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
                api='openai-responses',
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
        assert system.api == 'openai-responses'
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
