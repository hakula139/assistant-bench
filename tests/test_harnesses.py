import dataclasses
from importlib import metadata
from typing import Any

import pytest

from assistant_bench.catalog import SystemIdentity
from assistant_bench.generation import Generation
from assistant_bench.harnesses import HARNESSES, agent_kwargs, fingerprint, options
from support import make_generation, make_system


class TestFingerprint:
    @pytest.mark.parametrize(
        'change',
        [{'messages_protocol': 2}, {'anthropic_api_version': '2026-10-21'}],
        ids=['messages-protocol', 'api-version'],
    )
    @pytest.mark.parametrize('harness', ['generate', 'terminus-2'])
    def test_native_versions_invalidate_only_messages_outputs(
        self, change: dict[str, Any], harness: str
    ) -> None:
        versions = make_generation()
        changed = versions.model_copy(update=change)
        native = make_system(
            api='anthropic-messages',
            thinking_mode='anthropic-adaptive',
            harness=harness,
            generation=versions,
        )
        other = make_system(harness=harness, generation=versions)

        assert fingerprint(
            native.model_copy(update={'generation': changed})
        ) != fingerprint(native)
        assert fingerprint(
            other.model_copy(update={'generation': changed})
        ) == fingerprint(other)

    def test_captured_identity_stays_stable_when_the_runtime_changes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        generate = make_system()
        terminal = make_system(harness='terminus-2')
        before = (fingerprint(generate), fingerprint(terminal))

        def version(name: str) -> str:
            return '999.0.0'

        monkeypatch.setattr(metadata, 'version', version)

        after = (fingerprint(generate), fingerprint(terminal))

        assert after == before

    @pytest.mark.parametrize(
        ('changed', 'expected'),
        [
            pytest.param(
                make_generation(protocol=2, harbor='0.24.0'),
                (True, True),
                id='protocol',
            ),
            pytest.param(
                make_generation(protocol=1, harbor='0.25.0'),
                (False, True),
                id='harbor',
            ),
        ],
    )
    def test_versions_invalidate_only_the_outputs_they_shape(
        self, changed: Generation, expected: tuple[bool, bool]
    ) -> None:
        base = make_generation(protocol=1, harbor='0.24.0')

        differences = tuple(
            fingerprint(make_system(harness=harness, generation=base))
            != fingerprint(make_system(harness=harness, generation=changed))
            for harness in ('generate', 'terminus-2')
        )

        assert differences == expected

    def test_hashes_the_identity_of_a_system_under_any_name(self) -> None:
        assert fingerprint(make_system()) == 'b49c966f7ca680e0'
        assert fingerprint(make_system(name='x')) == 'b49c966f7ca680e0'
        assert fingerprint(make_system(display_name='x')) == 'b49c966f7ca680e0'
        assert fingerprint(make_system(vendor='other')) == 'b49c966f7ca680e0'

    @pytest.mark.parametrize(
        ('base', 'change', 'changes'),
        [
            pytest.param({}, {'route': 'acme/large-2'}, True, id='route'),
            pytest.param({}, {'effort': 'high'}, True, id='effort'),
            pytest.param({}, {'prompt': 'Be brief.'}, True, id='prompt'),
            pytest.param({}, {'harness': 'terminus-2'}, True, id='harness'),
            pytest.param({}, {'max_output_tokens': 128}, True, id='output'),
            pytest.param({}, {'api': 'openai-responses'}, True, id='api'),
            pytest.param(
                {'harness': 'terminus-2'},
                {'context_window': 16384},
                True,
                id='terminus-context',
            ),
            pytest.param({}, {'context_window': 16384}, False, id='context'),
        ],
    )
    def test_changes_only_with_what_shapes_the_requests(
        self, base: dict[str, str], change: dict[str, str | int], changes: bool
    ) -> None:
        before = make_system(**base)

        after = make_system(**(base | change))

        assert (fingerprint(after) != fingerprint(before)) is changes

    def test_hashes_every_kwarg_the_harness_builds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        system = make_system()
        before = fingerprint(system)

        def tuned(identity: SystemIdentity) -> dict[str, Any]:
            return options.generate(identity) | {'temperature': 0.5}

        monkeypatch.setitem(
            HARNESSES,
            'generate',
            dataclasses.replace(HARNESSES['generate'], options=tuned),
        )

        after = fingerprint(system)

        assert after != before


class TestAgentKwargs:
    @pytest.mark.parametrize('harness', ['generate', 'terminus-2'])
    def test_labels_the_harness_options_with_the_system_and_fingerprint(
        self, harness: str
    ) -> None:
        system = make_system(harness=harness)

        kwargs = agent_kwargs(system)

        assert kwargs == {
            'system': 'acme-large',
            'fingerprint': fingerprint(system),
        } | HARNESSES[harness].options(system)
