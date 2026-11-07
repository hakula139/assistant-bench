import textwrap
from pathlib import Path
from typing import Any

import pytest

from assistant_bench.catalog import (
    Api,
    NixosConfigExport,
    Price,
    SystemConfig,
    load_export,
    load_systems,
    resolve_system,
)
from assistant_bench.harnesses import fingerprint
from support import GENERATION, make_config, make_system


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

        with pytest.raises(ValueError, match='inside `nix develop`'):
            load_export()


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


class TestSystemConfig:
    @pytest.mark.parametrize(
        'fields',
        [
            {'harness': 'codex'},
            {'version': '1.2.3'},
            {'harness': 'codex', 'version': 'latest'},
            {'harness': 'codex', 'version': '../1.2.3'},
        ],
        ids=['missing-version', 'missing-harness', 'unpinned-version', 'path-version'],
    )
    def test_requires_a_pinned_version_with_an_installed_harness(
        self, fields: dict[str, Any]
    ) -> None:
        with pytest.raises(ValueError):
            make_config(**fields)


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
                display_name = "Acme Large"

                [small]
                model = "acme-small"
                display_name = "Acme Small"
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
                display_name = "Acme Large"

                [large-codex]
                model = "acme-large"
                prompt = "nixos-config:codex"
                display_name = "Acme Large"
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
                display_name = "Acme Large"

                [broken]
                effort = "low"
                display_name = "Broken"
                """,
                '`systems.toml`: `broken.model`: Field required.',
                id='invalid-table',
            ),
            pytest.param(
                """\
                [large]
                model = "acme-large"
                display_name = " "
                """,
                '`systems.toml`: `large.display_name`: String should have at least 1 '
                'character.',
                id='blank-display-name',
            ),
            pytest.param(
                """\
                [large]
                model = "acme-large"
                display_name = "Large"
                prompt = 5
                """,
                '`systems.toml`: `large.prompt`: Input should be a valid string.',
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


class TestResolveSystem:
    def test_resolves_native_capacities_and_thinking_from_catalog_metadata(
        self, export: NixosConfigExport
    ) -> None:
        config = make_config(api='anthropic-messages', effort='high')

        resolved = resolve_system('native', config, export, GENERATION)

        assert resolved == make_system(
            name='native',
            api='anthropic-messages',
            effort='high',
            thinking_mode='anthropic-adaptive',
        )

    @pytest.mark.parametrize(
        ('model', 'effort', 'expected'),
        [
            ('acme-large', 'high', 'high'),
            ('acme-large', None, 'medium'),
            ('acme-small', 'low', 'low'),
            ('acme-small', None, None),
            ('local-model', None, None),
        ],
        ids=[
            'accepted-effort',
            'required-default',
            'accepted-optional-effort',
            'optional-omission',
            'no-effort-rules',
        ],
    )
    def test_resolves_the_effort_the_model_accepts(
        self,
        export: NixosConfigExport,
        model: str,
        effort: str | None,
        expected: str | None,
    ) -> None:
        config = make_config(model=model, effort=effort)

        system = resolve_system('x', config, export, GENERATION)

        assert system.effort == expected

    def test_ignores_apis_the_bench_cannot_select(
        self, export: NixosConfigExport
    ) -> None:
        document = export.model_dump(by_alias=True)
        document['catalog']['models']['acme-large']['apis'].append(
            'google-generate-content'
        )
        configured = NixosConfigExport.model_validate(document)
        config = make_config()

        system = resolve_system('large', config, configured, GENERATION)

        assert system.api == 'openai-chat-completions'

    @pytest.mark.parametrize(
        ('top', 'model', 'config'),
        [
            ({}, {'channelCost': {}}, {}),
            ({}, {}, {'display_name': 'Other label'}),
            ({}, {'vendor': 'other'}, {}),
            ({'rev': 'another-revision'}, {}, {}),
        ],
        ids=['price', 'display-name', 'vendor', 'export-revision'],
    )
    def test_keeps_the_fingerprint_when_only_public_metadata_changes(
        self,
        export: NixosConfigExport,
        top: dict[str, str],
        model: dict[str, Any],
        config: dict[str, str],
    ) -> None:
        original = make_config()
        document: dict[str, Any] = export.model_dump(by_alias=True) | top
        document['catalog']['models']['acme-large'].update(model)
        changed = NixosConfigExport.model_validate(document)

        system = resolve_system(
            'large', original.model_copy(update=config), changed, GENERATION
        )

        assert fingerprint(system) == fingerprint(
            resolve_system('large', original, export, GENERATION)
        )

    def test_reports_a_zero_priced_channel_as_unpriced(
        self, export: NixosConfigExport
    ) -> None:
        system = resolve_system(
            'local',
            make_config(model='local-model'),
            export,
            GENERATION,
        )

        assert system.route == 'local/model'
        assert system.vendor == 'other'
        assert system.price is None

    def test_reads_a_nixos_config_system_prompt(
        self, export: NixosConfigExport
    ) -> None:
        system = resolve_system(
            'large',
            make_config(prompt='nixos-config:claudeCode'),
            export,
            GENERATION,
        )

        assert system.prompt == '# CLAUDE.md\n\nBe direct.'

    @pytest.mark.parametrize(
        ('config', 'message'),
        [
            (
                make_config(model='acme-huge'),
                'System `x`: model `acme-huge` is not in the nixos-config catalog, '
                '`data/llm-models.nix`. Check it against `acme-large`, '
                '`acme-routed`, `acme-unrouted`.',
            ),
            (
                make_config(model='zeta'),
                'System `x`: model `zeta` is not in the nixos-config catalog, '
                '`data/llm-models.nix`.',
            ),
            (
                make_config(model='acme-small', effort='medium'),
                'System `x`: effort `medium` is not one of `low`, `high`.',
            ),
            (
                make_config(model='local-model', effort='low'),
                'System `x`: the model takes no effort.',
            ),
            (
                make_config(model='acme-unrouted'),
                'System `x`: catalog model `acme-unrouted` has no channel. Name a '
                'catalog model with one channel.',
            ),
            (
                make_config(model='acme-routed'),
                'System `x`: catalog model `acme-routed` has channels `acme`, '
                '`router`. Name a catalog model with one channel.',
            ),
            (
                make_config(prompt='nixos-config:omp'),
                'System `x`: nixos-config has no `omp` instructions. Choose among '
                '`claudeCode`, `codex`.',
            ),
            (
                make_config(prompt='prompts/brief.md'),
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
            resolve_system('x', config, export, GENERATION)

        assert str(error.value) == message

    @pytest.mark.parametrize(
        ('model', 'thinking', 'api', 'message'),
        [
            (
                {'apis': ['openai-chat-completions']},
                {},
                'anthropic-messages',
                'System `x`: API `anthropic-messages` is not one of '
                '`openai-chat-completions`.',
            ),
            (
                {'apis': ['google-generate-content']},
                {},
                'openai-chat-completions',
                'System `x`: API `openai-chat-completions` is not one of '
                '`google-generate-content`.',
            ),
            (
                {'apis': []},
                {},
                'openai-chat-completions',
                'System `x`: the catalog model declares no supported APIs.',
            ),
            (
                {},
                {'defaultLevel': 'max'},
                'openai-chat-completions',
                'System `x`: effort `max` is not one of `low`, `medium`, `high`.',
            ),
            (
                {},
                {'mode': 'effort'},
                'anthropic-messages',
                'System `x`: native Messages requires the `anthropic-adaptive` '
                'thinking mode.',
            ),
            (
                {'maxTokens': 8192},
                {},
                'openai-chat-completions',
                'System `x`: `max_output_tokens` must be less than `context_window`.',
            ),
        ],
        ids=[
            'unlisted-api',
            'only-unselectable-apis',
            'no-apis',
            'unoffered-required-default',
            'no-adaptive-mode',
            'no-prompt-room',
        ],
    )
    def test_rejects_metadata_that_cannot_resolve(
        self,
        export: NixosConfigExport,
        model: dict[str, Any],
        thinking: dict[str, str],
        api: Api,
        message: str,
    ) -> None:
        document = export.model_dump(by_alias=True)
        entry = document['catalog']['models']['acme-large']
        entry.update(model)
        entry['thinking'].update(thinking)
        configured = NixosConfigExport.model_validate(document)
        config = make_config(api=api)

        with pytest.raises(ValueError) as error:
            resolve_system('x', config, configured, GENERATION)

        assert str(error.value) == message
