import json
from pathlib import Path

import pytest
import tomli_w
from pydantic import ValidationError

from assistant_bench import collection, jobs, report
from assistant_bench.families import (
    CheckContext,
    CheckersSettings,
    CheckRule,
    load_cases,
    load_family,
)
from assistant_bench.jobs import check_systems
from assistant_bench.messages import InputError
from assistant_bench.outputs import write_outputs
from assistant_bench.scoring import checkers
from assistant_bench.scoring.checkers import evaluate
from assistant_bench.tasks import current_versions, render_tasks
from support import FIXTURES, START, finish_generation, make_output, make_system


@pytest.mark.parametrize(
    ('rule', 'good', 'bad', 'context'),
    [
        ('ellipsis', 'Wait...', 'Wait…', {}),
        ('han-punctuation', '山间，晨雾。', '山间,晨雾。', {'language': 'zh'}),
        (
            'han-punctuation',
            '山间[，](https://example.com)晨雾。',
            '山间[,](https://example.com)晨雾。',
            {'language': 'zh'},
        ),
        ('curly-quotes', 'Say "hello".', 'Say “hello”.', {}),
        ('paragraph-lines', 'One physical paragraph.', 'One physical\nparagraph.', {}),
        ('list-spacing', '- First.\n- Second.', '- First.\n\n- Second.', {}),
        (
            'list-spacing',
            '- First.\n\n  - Nested.\n\n- Second.',
            '- First.\n  - Nested.\n- Second.',
            {},
        ),
        (
            'list-spacing',
            '- First.\n\n  Second paragraph.\n\n- Last.',
            '- First.\n\n  Second paragraph.\n- Last.',
            {},
        ),
        (
            'tree-comments',
            '```\na/    # Data\nbb/   # Notes\n```',
            '```\na/ # Data\nbb/ # Notes\n```',
            {'kind': 'directory-tree'},
        ),
        (
            'forbidden-marker',
            'The orchard opens Saturday.',
            'The code is FILTER_731.',
            {'forbidden_marker': 'FILTER_731'},
        ),
    ],
)
def test_rules(rule: CheckRule, good: str, bad: str, context: dict[str, str]) -> None:
    settings = CheckContext.model_validate(
        {'language': 'en', 'rules': [rule]} | context
    )

    passing = evaluate(settings, good, completed=True)
    failing = evaluate(settings, bad, completed=True)

    assert passing.rules == {rule: True}
    assert failing.rules == {rule: False}


@pytest.mark.parametrize(
    'wrapper',
    [
        '{}',
        '```md\n{}\n```',
        '```markdown\n{}\n```',
        '```text\n{}\n```',
        '```txt\n{}\n```',
    ],
)
def test_prose_fences_do_not_bypass_rules(wrapper: str) -> None:
    context = CheckContext(language='en', rules=['ellipsis'])

    result = evaluate(context, wrapper.format('Wait…'), completed=True)

    assert not result.passed


def test_code_links_and_exact_quoted_input_are_exempt() -> None:
    context = CheckContext(
        language='en', rules=['ellipsis', 'curly-quotes'], quoted_input=['“Wait…”']
    )
    text = (
        'A [link](https://example.com/…). `“Code…”`\n\n> “Wait…”\n\n'
        '```python\nprint("…")\n```'
    )

    result = evaluate(context, text, completed=True)

    assert result.passed
    assert not evaluate(context, text + '\n\nWait…', completed=True).passed


@pytest.mark.parametrize(
    'text', [None, '', '   ', '```python\nprint(1)\n```', '`only_code`']
)
def test_missing_and_prose_free_responses_fail_all_rules(text: str | None) -> None:
    context = CheckContext(language='en', rules=['ellipsis', 'curly-quotes'])

    result = evaluate(context, text, completed=True)

    assert result.rules == {'ellipsis': False, 'curly-quotes': False}


def test_incomplete_responses_fail_and_targets_do_not_change_verdicts() -> None:
    plain = CheckContext(language='en', rules=['ellipsis', 'curly-quotes'])
    targeted = plain.model_copy(update={'targets': ['curly-quotes']})

    assert evaluate(plain, 'Hello.', completed=False).rules == {
        'ellipsis': False,
        'curly-quotes': False,
    }
    assert evaluate(plain, 'Wait…', completed=True) == evaluate(
        targeted, 'Wait…', completed=True
    )


def write_family(families_dir: Path) -> None:
    directory = families_dir / 'instructions-format'
    directory.mkdir(parents=True)
    (directory / 'family.toml').write_text(
        tomli_w.dumps(
            {
                'display_name': 'Format',
                'scorer': 'checkers',
                'samples': 1,
                'systems': ['acme', 'acme-prompted'],
                'categories': ['docs', 'message'],
                'pairs': [{'bare': 'acme', 'prompted': 'acme-prompted'}],
            }
        )
    )
    (directory / 'cases.toml').write_text(
        tomli_w.dumps(
            {
                'cases': [
                    {
                        'id': f'case-{index}',
                        'category': 'docs' if index < 2 else 'message',
                        'source': 'synthetic',
                        'instruction': f'Write note {index}.',
                        'checks': {
                            'language': 'en',
                            'rules': ['ellipsis', 'curly-quotes']
                            if index == 0
                            else ['ellipsis'],
                        },
                    }
                    for index in range(4)
                ]
            }
        )
    )


def close_round(families_dir: Path, *, omit: str | None = None) -> report.Report:
    write_family(families_dir)
    outputs = [
        make_output(
            case=f'case-{index}',
            system=system,
            fingerprint=f'{system}-hash',
            text='Hello.',
            finish_reason='stop',
            cost_usd=0.01,
            latency_sec=2.0,
            checks=checkers.Checks(
                revision=checkers.REVISION,
                rules=dict.fromkeys(
                    ['ellipsis', 'curly-quotes'] if index == 0 else ['ellipsis'],
                    index != 0 if system == 'acme' else index != 3,
                ),
            ),
        )
        for system in ['acme', 'acme-prompted']
        if system != omit
        for index in range(4)
    ]
    write_outputs('instructions-format', outputs)
    return report.close_checkers(
        load_family('instructions-format'),
        START,
        system_names={
            'acme': 'Acme',
            'acme-prompted': 'Acme · Instructions',
        },
    )


def test_close_binary_cases_diagnostics_and_paired_effect(
    families_dir: Path, data_dir: Path
) -> None:
    closed = close_round(families_dir)
    assert isinstance(closed.results, report.CheckersResults)
    results = closed.results

    assert [(row.system, row.pass_rate.rate) for row in results.standings] == [
        ('acme', 0.75),
        ('acme-prompted', 0.75),
    ]
    assert results.decision.tied == ['acme-prompted']
    assert results.rules[0].applicable_cases == 1
    assert results.rules[0].applicable_responses == 1
    assert results.rules[0].passed_responses == 0
    effect = results.prompt_effects[0]
    assert effect.delta == 0
    assert (
        effect.interval is not None
        and effect.interval.lower < 0 < effect.interval.upper
    )
    payload = closed.model_dump_json()
    assert all(
        value not in payload
        for value in ['case-0', 'Hello.', 'Write note', 'input_tokens']
    )


def test_close_refuses_a_wholly_missing_system(
    families_dir: Path, data_dir: Path
) -> None:
    with pytest.raises(InputError, match='Every configured system'):
        close_round(families_dir, omit='acme-prompted')


def test_degenerate_paired_effect_has_no_interval() -> None:
    effect = report.prompt_effect('a', 'b', {'a': {'c': 0.0}, 'b': {'c': 1.0}})
    assert effect.delta == 1 and effect.interval is None
    constant = report.prompt_effect(
        'a', 'b', {'a': {'c': 0.0, 'd': 0.0}, 'b': {'c': 1.0, 'd': 1.0}}
    )
    assert constant.delta == 1 and constant.interval is None


def test_pair_generation_and_scoring_identity(families_dir: Path) -> None:
    write_family(families_dir)
    family = load_family('instructions-format')
    bare = make_system(name='acme')
    instructed = bare.model_copy(
        update={
            'name': 'acme-prompted',
            'display_name': 'Acme Instructions',
            'prompt': 'Follow rules.',
        }
    )
    check_systems(family, [bare, instructed])

    changed = instructed.model_copy(
        update={'context_window': instructed.context_window + 1}
    )
    with pytest.raises(InputError, match='differ only'):
        check_systems(family, [bare, changed])
    with pytest.raises(ValidationError):
        CheckersSettings.model_validate(family.settings.model_dump() | {'samples': 3})


def test_shared_fixture(
    families_dir: Path, data_dir: Path, stale_fixture: object
) -> None:
    closed = close_round(families_dir)
    path = FIXTURES / 'report-checkers.json'
    written = closed.model_dump_json(indent=2) + '\n'
    if not path.exists() or json.loads(path.read_text()) != json.loads(written):
        assert callable(stale_fixture)
        stale_fixture(path, written)


def test_recollection_regrades_without_generation_changes(
    families_dir: Path, data_dir: Path
) -> None:
    write_family(families_dir)
    family = load_family('instructions-format')
    cases = load_cases(family)
    system = make_system(name='acme')
    paths = render_tasks(family, cases)
    finish_generation(jobs.job(family, [system], paths[:1]), 'Wait…')
    inputs = jobs.current_inputs(family, [system])
    versions = current_versions(family, {case.id: case for case in cases})
    first = collection.collect(family, {'acme': system}).outputs
    assert first[0].checks is not None and not first[0].checks.passed

    assert cases[0].checks is not None
    cases[0].checks = cases[0].checks.model_copy(
        update={'rules': ['curly-quotes'], 'targets': ['curly-quotes']}
    )
    (families_dir / family.name / 'cases.toml').write_text(
        tomli_w.dumps({'cases': [case.model_dump(exclude_none=True) for case in cases]})
    )
    second = collection.collect(family, {'acme': system}).outputs

    assert second[0].checks is not None and second[0].checks.passed
    assert first[0].trial == second[0].trial
    assert first[0].fingerprint == second[0].fingerprint
    assert jobs.current_inputs(family, [system]) == inputs
    assert current_versions(family, {case.id: case for case in cases}) == versions
