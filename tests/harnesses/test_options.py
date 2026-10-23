from assistant_bench.families import TaskTestsSettings
from assistant_bench.harnesses import options
from support import make_system


SETTINGS = TaskTestsSettings(
    scorer='tests', display_name='Family', samples=1, max_output_tokens=64
)


class TestGenerate:
    def test_builds_every_request_setting(self) -> None:
        system = make_system(prompt='Be brief.')

        assert options.generate(system, SETTINGS) == {
            'system': 'acme-large',
            'fingerprint': system.fingerprint,
            'effort': 'medium',
            'system_prompt': 'Be brief.',
            'max_output_tokens': 64,
        }

    def test_keeps_a_missing_setting_as_none(self) -> None:
        system = make_system(effort=None, prompt=None)

        assert options.generate(system, SETTINGS) == {
            'system': 'acme-large',
            'fingerprint': system.fingerprint,
            'effort': None,
            'system_prompt': None,
            'max_output_tokens': 64,
        }


class TestTerminus:
    def test_builds_the_kwargs_a_system_yields(self) -> None:
        system = make_system(harness='terminus-2')

        assert options.terminus(system, SETTINGS) == {
            'system': 'acme-large',
            'fingerprint': system.fingerprint,
            'llm_call_kwargs': {'max_completion_tokens': 64},
            'reasoning_effort': 'medium',
            'record_terminal_session': False,
            'suppress_max_turns_warning': True,
        }

    def test_omits_the_effort_the_catalog_resolves_none(self) -> None:
        system = make_system(harness='terminus-2', effort=None)

        assert 'reasoning_effort' not in options.terminus(system, SETTINGS)
