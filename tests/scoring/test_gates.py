import textwrap

import pytest

from assistant_bench.families import Gate, Gates, VotesSettings
from assistant_bench.scoring.gates import (
    detect_language,
    failed_gates,
    traditional_characters,
)


ZH = VotesSettings(
    scorer='votes',
    display_name='Family F',
    systems=['acme-large'],
    samples=1,
    reference='acme-large',
    language='zh',
    gates=Gates(min_chars=10, max_chars=60, script='hans'),
)
EN = VotesSettings(
    scorer='votes',
    display_name='Family F',
    systems=['acme-large'],
    samples=1,
    reference='acme-large',
    language='en',
    gates=Gates(),
)
GUIDE_URL = 'https://example.com/docs/release/notes/install/upgrade/guide'
ZH_REPLY = '我把 `widget` 升到了 2.0.1，并更新了锁文件。Windows 版本还没有测试。'


class TestFailedGates:
    def test_passes_a_clean_reply(self) -> None:
        assert failed_gates(ZH, ZH_REPLY, 'stop', '') == []

    @pytest.mark.parametrize(
        ('finish_reason', 'failures'),
        [('stop', [Gate.EMPTY]), ('length', [Gate.EMPTY, Gate.TRUNCATED])],
        ids=['stop', 'length'],
    )
    def test_rejects_a_blank_output_without_further_checks(
        self, finish_reason: str, failures: list[Gate]
    ) -> None:
        assert failed_gates(ZH, ' \n\t', finish_reason, '') == failures

    @pytest.mark.parametrize('finish_reason', ['length', 'content_filter', None])
    def test_rejects_any_finish_but_stop(self, finish_reason: str | None) -> None:
        assert failed_gates(ZH, ZH_REPLY, finish_reason, '') == [Gate.TRUNCATED]

    @pytest.mark.parametrize(
        ('text', 'failures'),
        [
            (' \n' + '短' * 10 + '\n ', []),
            ('    ' + '短' * 9 + '    ', [Gate.LENGTH]),
            ('  ' + '长' * 60 + '  ', []),
            ('长' * 61, [Gate.LENGTH]),
        ],
        ids=['min', 'below-min-padded', 'max-padded', 'above-max'],
    )
    def test_bounds_the_stripped_length(self, text: str, failures: list[Gate]) -> None:
        assert failed_gates(ZH, text, 'stop', '') == failures

    def test_rejects_an_output_in_another_language(self) -> None:
        text = 'I bumped widget to 2.0.1 and updated the lockfile.'

        assert failed_gates(ZH, text, 'stop', '') == [Gate.LANGUAGE]

    @pytest.mark.parametrize(
        'tag', [None, 'p', 'div'], ids=['plain', 'paragraph', 'block']
    )
    def test_rejects_another_language_in_visible_html(self, tag: str | None) -> None:
        settings = EN.model_copy(update={'gates': Gates(min_chars=80)})
        prose = '构建已经通过，部署前还需要检查服务状态和配置文件。' * 4
        text = prose if tag is None else f'<{tag}>{prose}</{tag}>'

        assert failed_gates(settings, text, 'stop', '') == [Gate.LANGUAGE]

    def test_rejects_traditional_characters_absent_from_the_instruction(
        self,
    ) -> None:
        text = '這個改動已經構建過了，但還沒有提交。'

        assert failed_gates(ZH, text, 'stop', '') == [Gate.SCRIPT]
        assert (
            failed_gates(ZH, text, 'stop', '這個改動已經構建過了，但還沒有提交') == []
        )

    def test_checks_only_the_gates_the_family_sets(self) -> None:
        text = 'The sign read 這個 in Traditional script. ' * 10

        assert failed_gates(EN, text, 'stop', '') == []

    def test_lists_every_failure_in_gate_order(self) -> None:
        text = 'Finished, though the 測試 suite was not run on macOS or anywhere'

        assert failed_gates(ZH, text, 'length', '') == [
            Gate.TRUNCATED,
            Gate.LENGTH,
            Gate.LANGUAGE,
            Gate.SCRIPT,
        ]


class TestDetectLanguage:
    @pytest.mark.parametrize(
        'text',
        [
            f'构建通过，详见 {GUIDE_URL}',
            '构建通过，运行 `cargo test --workspace --all-features --release --locked '
            '--offline --quiet` 即可',
            textwrap.dedent(
                f"""\
                ```text
                构建通过，详见 {GUIDE_URL}
                ```
                """
            ),
            'Docs at https://example.com/a，安装方法和迁移说明都在发布页里，旧配置仍可读取',
            textwrap.dedent(
                f"""\
                ```python
                # 构建通过，详见 {GUIDE_URL}
                ```
                """
            ),
            textwrap.dedent(
                f"""\
                ```javascript
                // 构建通过，详见 {GUIDE_URL}
                ```
                """
            ),
            textwrap.dedent(
                f"""\
                ```javascript
                /* 构建通过，详见 {GUIDE_URL} */
                ```
                """
            ),
            textwrap.dedent(
                f'''\
                ```python
                """构建通过，详见 {GUIDE_URL}"""
                ```
                '''
            ),
        ],
        ids=[
            'url',
            'inline-code',
            'prose-fence',
            'url-ending-at-chinese-text',
            'python-line-comment',
            'javascript-line-comment',
            'block-comment',
            'docstring',
        ],
    )
    def test_ignores_urls_and_inline_code_in_prose(self, text: str) -> None:
        assert detect_language(text) == 'zh'

    @pytest.mark.parametrize(
        ('text', 'language'),
        [
            ('<div><p>构建通过，<span>部署说明已更新</span>。</p></div>', 'zh'),
            ('<p>&#x6784;&#x5efa;&#x901a;&#x8fc7;</p>', 'zh'),
            (
                '<p>构建通过，详见 https://example.com/release/notes/install/guide</p>',
                'zh',
            ),
            ('<p>The build passed and the deployment guide is current.</p>', 'en'),
            (
                '<div>构建通过<script>English words in hidden script content</script>'
                '<style>English words in hidden style content</style></div>',
                'zh',
            ),
            (
                '构建通过 <script>English words in hidden script content</script> '
                '<style>English words in hidden style content</style>',
                'zh',
            ),
            (
                '构建 <span>通过</span> '
                '<code>English identifiers are excluded from prose counts</code>',
                'zh',
            ),
            (
                textwrap.dedent(
                    """\
                    <pre><code>
                    English identifiers are excluded from prose counts
                    </code> more code identifiers remain excluded here</pre>
                    <p>构建通过</p>
                    """
                ),
                'zh',
            ),
            ('<!-- English words inside a markup comment --><p>构建通过</p>', 'zh'),
            ('<p>&lt;widget&gt; 构建通过</p>', 'zh'),
            (
                '构建通过 &lt;script&gt;English visible words in escaped markup'
                '&lt;/script&gt;',
                'en',
            ),
            (
                textwrap.dedent(
                    """\
                    构建通过 <code>English identifiers

                    more English words in code

                    </code>
                    """
                ),
                'zh',
            ),
            (
                textwrap.dedent(
                    """\
                    <div><code>English identifiers

                    more English words in code

                    </code></div>

                    构建通过
                    """
                ),
                'zh',
            ),
            (
                textwrap.dedent(
                    """\
                    构建通过 <code>

                    ```python
                    # English words inside a fenced code comment
                    ```

                    </code>
                    """
                ),
                'zh',
            ),
            (
                textwrap.dedent(
                    """\
                    构建通过 <code>

                    ```text
                    English words inside a prose fence
                    ```

                    </code>
                    """
                ),
                'zh',
            ),
            (
                textwrap.dedent(
                    """\
                    ```markdown
                    <code>English identifiers
                    ```

                    构建通过
                    """
                ),
                'zh',
            ),
        ],
        ids=[
            'nested-block',
            'character-references',
            'url',
            'english',
            'hidden-block-content',
            'hidden-inline-content',
            'inline-code',
            'block-code',
            'markup-comment',
            'escaped-markup',
            'escaped-inline-markup',
            'code-across-inline-groups',
            'code-across-block-groups',
            'code-containing-fenced-comment',
            'code-containing-prose-fence',
            'isolated-prose-fence-state',
        ],
    )
    def test_reads_visible_html_prose(self, text: str, language: str) -> None:
        assert detect_language(text) == language

    def test_does_not_reuse_html_state_across_outputs(self) -> None:
        hidden = '<code>English words inside an unclosed code element'
        visible = 'English words in a separate output'

        languages = [detect_language(hidden), detect_language(visible)]

        assert languages == [None, 'en']

    def test_reads_english_prose_quoting_chinese(self) -> None:
        text = 'The error message 文件不存在 means the file does not exist yet.'

        assert detect_language(text) == 'en'

    @pytest.mark.parametrize(
        ('comment', 'language'),
        [
            ('接口限流为每秒两次，留出余量', 'zh'),
            ('Rate limit of two per second', 'en'),
        ],
        ids=['zh', 'en'],
    )
    def test_judges_code_by_its_comments(self, comment: str, language: str) -> None:
        text = textwrap.dedent(
            f"""\
            ```python
            def fetch(client, cursor=None):
                # {comment}
                time.sleep(0.55)
                return client.get('/items', params={{'cursor': cursor}})
            ```
            """
        )

        assert detect_language(text) == language

    @pytest.mark.parametrize(
        'code',
        [
            '#!/usr/bin/env rust-script\n#[derive(Debug)]\nstruct Retry; // 重试',
            '#include <stdio.h>\n#define BUFFER_SIZE 4096\n// 缓冲区',
            '/* 主色 */\n.button { color: #fff; background: #0a84ff; }',
            'fn main() { let page = 4096; } /* 缓冲区按页对齐 */',
            'def parse(raw):\n    """解析原始字节。"""',
            "def parse(raw):\n    '''解析原始字节。'''",
        ],
        ids=[
            'shebang-and-attribute',
            'preprocessor',
            'css-colors',
            'block-comment',
            'double-quoted-docstring',
            'single-quoted-docstring',
        ],
    )
    def test_reads_the_comments_of_fenced_code_alone(self, code: str) -> None:
        assert detect_language(f'```\n{code}\n```\n') == 'zh'

    def test_reads_an_indented_code_block_as_code(self) -> None:
        code = '    # 先清理构建缓存\n    make clean --jobs 8 --keep-going\n'
        text = f'Run this first:\n\n{code}'

        assert detect_language(text) == 'zh'

    @pytest.mark.parametrize(
        ('fence', 'nested'), [('````', '```'), ('```', '```')], ids=['valid', 'broken']
    )
    def test_reads_a_markdown_fence_as_markdown(self, fence: str, nested: str) -> None:
        commands = (
            'pnpm run lint --fix --cache --max-warnings 0 src tests scripts\n' * 4
        )
        text = (
            f'{fence}markdown\n'
            '## 本地开发\n\n'
            '需要 Node.js 24、pnpm 11 和本机运行的 Docker。\n\n'
            f'{nested}bash\npnpm install\ndocker compose up -d\n{commands}{nested}\n\n'
            '开发服务器在 `http://localhost:5173`，接口代理到 `http://localhost:8080`。'
            '端口冲突时在 `.env.local` 里改 `PORT` 和 `API_PORT`。\n'
            f'{fence}\n'
        )

        assert detect_language(text) == 'zh'

    @pytest.mark.parametrize('tag', ['text', 'MARKDOWN', 'text title=commit'])
    def test_reads_a_prose_fence_whole(self, tag: str) -> None:
        text = textwrap.dedent(
            f"""\
            提交信息如下：

            ```{tag}
            fix: reject a directory passed to the output flag

            The export now checks the path before it starts writing.
            ```
            """
        )

        assert detect_language(text) == 'en'

    @pytest.mark.parametrize(
        'text',
        [
            '```\nx = 1\n```\n`y` 42',
            '<pre><code>Only code identifiers</code></pre>',
            '你好 hello there',
        ],
        ids=['code-and-numbers', 'html-code', 'even-split'],
    )
    def test_returns_none_without_a_majority(self, text: str) -> None:
        assert detect_language(text) is None


class TestTraditionalCharacters:
    @pytest.mark.parametrize(
        ('text', 'source', 'characters'),
        [
            ('這個設定，這個測試', '', ['這', '個', '設', '測', '試']),
            ('乾坤未定，著作等身，於是动身', '', []),
            ('请把「臺北」改成「台北」', '原文写作臺北', []),
        ],
        ids=['each-once', 'valid-in-both-scripts', 'in-the-source'],
    )
    def test_lists_each_traditional_only_character_the_source_lacks(
        self, text: str, source: str, characters: list[str]
    ) -> None:
        assert traditional_characters(text, source) == characters
