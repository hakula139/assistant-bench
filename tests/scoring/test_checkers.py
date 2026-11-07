import pytest

from assistant_bench.families import CheckContext, CheckRule
from assistant_bench.scoring.checkers import evaluate


class TestEvaluate:
    @pytest.mark.parametrize(
        'text',
        [
            '山间, 晨雾。',
            '山间 ,晨雾。',
            '山间\t:\t晨雾。',
            '山间,\n晨雾。',
            '山间,  \n晨雾。',
            '山间[,](https://example.com) 晨雾。',
        ],
    )
    def test_han_punctuation_checks_whitespace_inside_one_prose_span(
        self, text: str
    ) -> None:
        context = CheckContext(language='zh', rules=['han-punctuation'])

        result = evaluate(context, text, completed=True)

        assert not result.passed

    @pytest.mark.parametrize(
        'text',
        [
            '山间,\n\n晨雾。',
            '山间, `原文` 晨雾。',
            '山间, ![原文](https://example.com/image.png) 晨雾。',
            '山间, **保留,原文…** 晨雾。',
            '山间, **保留,原文…**\n晨雾。',
        ],
    )
    def test_han_punctuation_preserves_paragraph_and_protected_boundaries(
        self, text: str
    ) -> None:
        context = CheckContext(
            language='zh',
            rules=['han-punctuation', 'ellipsis'],
            quoted_input=['**保留,原文…**'],
        )

        result = evaluate(context, text, completed=True)

        assert result.passed

    def test_an_exact_quote_alone_is_not_authored_prose(self) -> None:
        context = CheckContext(
            language='en', rules=['ellipsis'], quoted_input=['**Wait…**']
        )

        result = evaluate(context, '**Wait…**', completed=True)

        assert not result.passed

    @pytest.mark.parametrize(
        ('rule', 'good', 'bad', 'context'),
        [
            ('ellipsis', 'Wait...', 'Wait…', {}),
            ('han-punctuation', '山间，晨雾。', '山间,晨雾。', {'language': 'zh'}),
            ('han-punctuation', '𠀀，晨。', '𠀀,晨。', {'language': 'zh'}),
            (
                'han-punctuation',
                '山间[，](https://example.com)晨雾。',
                '山间[,](https://example.com)晨雾。',
                {'language': 'zh'},
            ),
            ('curly-quotes', 'Say "hello".', 'Say “hello”.', {}),
            (
                'paragraph-lines',
                'One physical paragraph.',
                'One physical\nparagraph.',
                {},
            ),
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
    def test_rules(
        self, rule: CheckRule, good: str, bad: str, context: dict[str, str]
    ) -> None:
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
    def test_prose_fences_do_not_bypass_rules(self, wrapper: str) -> None:
        context = CheckContext(language='en', rules=['ellipsis'])

        result = evaluate(context, wrapper.format('Wait…'), completed=True)

        assert not result.passed
        assert evaluate(context, wrapper.format('Wait...'), completed=True).passed

    def test_code_links_and_exact_quoted_input_are_exempt(self) -> None:
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
    def test_missing_and_prose_free_responses_fail_all_rules(
        self, text: str | None
    ) -> None:
        context = CheckContext(language='en', rules=['ellipsis', 'curly-quotes'])

        result = evaluate(context, text, completed=True)

        assert result.rules == {'ellipsis': False, 'curly-quotes': False}

    def test_incomplete_responses_fail_and_targets_do_not_change_verdicts(self) -> None:
        plain = CheckContext(language='en', rules=['ellipsis', 'curly-quotes'])
        targeted = plain.model_copy(update={'targets': ['curly-quotes']})

        assert evaluate(plain, 'Hello.', completed=False).rules == {
            'ellipsis': False,
            'curly-quotes': False,
        }
        assert evaluate(plain, 'Wait…', completed=True) == evaluate(
            targeted, 'Wait…', completed=True
        )
