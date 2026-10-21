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
    samples=1,
    max_output_tokens=300,
    reference='acme-large',
    language='zh',
    gates=Gates(min_chars=10, max_chars=60, script='hans'),
)
EN = VotesSettings(
    scorer='votes',
    samples=1,
    max_output_tokens=300,
    reference='acme-large',
    language='en',
    gates=Gates(),
)
ZH_REPLY = '我把 `widget` 升到了 2.0.1，并更新了锁文件。Windows 版本还没有测试。'


class TestFailedGates:
    def test_passes_a_clean_reply(self) -> None:
        assert failed_gates(ZH, ZH_REPLY, 'stop', '') == []

    @pytest.mark.parametrize(
        ('finish_reason', 'failures'),
        [('stop', [Gate.EMPTY]), ('length', [Gate.EMPTY, Gate.TRUNCATED])],
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
            '构建通过，详见 https://example.com/docs/release/notes/install/upgrade/guide',
            '构建通过，运行 `cargo test --workspace --all-features --release --locked '
            '--offline --quiet` 即可',
        ],
        ids=['url', 'inline-code'],
    )
    def test_ignores_urls_and_inline_code_in_prose(self, text: str) -> None:
        assert detect_language(text) == 'zh'

    def test_ends_a_url_at_chinese_text(self) -> None:
        text = 'Docs at https://example.com/a，安装方法和迁移说明都在发布页里，旧配置仍可读取'

        assert detect_language(text) == 'zh'

    def test_reads_english_prose_quoting_chinese(self) -> None:
        text = 'The error message 文件不存在 means the file does not exist yet.'

        assert detect_language(text) == 'en'

    @pytest.mark.parametrize(
        ('comment', 'language'),
        [
            ('接口限流为每秒两次，留出余量', 'zh'),
            ('Rate limit of two per second', 'en'),
        ],
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
        ],
        ids=['shebang-and-attribute', 'preprocessor', 'css-colors'],
    )
    def test_skips_hashes_that_open_no_comment(self, code: str) -> None:
        assert detect_language(f'```\n{code}\n```\n') == 'zh'

    @pytest.mark.parametrize(
        'code',
        [
            'fn main() { let page = 4096; } /* 缓冲区按页对齐 */',
            'def parse(raw):\n    """解析原始字节。"""',
            "def parse(raw):\n    '''解析原始字节。'''",
        ],
        ids=['block-comment', 'double-quoted-docstring', 'single-quoted-docstring'],
    )
    def test_reads_block_comments_and_docstrings(self, code: str) -> None:
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

    def test_ignores_urls_inside_a_prose_fence(self) -> None:
        url = 'https://example.com/docs/release/notes/install/upgrade/guide'

        assert detect_language(f'```text\n构建通过，详见 {url}\n```\n') == 'zh'

    @pytest.mark.parametrize('text', ['```\nx = 1\n```\n`y` 42', '你好 hello there'])
    def test_returns_none_without_a_majority(self, text: str) -> None:
        assert detect_language(text) is None


class TestTraditionalCharacters:
    def test_lists_each_traditional_only_character_once(self) -> None:
        assert traditional_characters('這個設定，這個測試', '') == [
            '這',
            '個',
            '設',
            '測',
            '試',
        ]

    def test_never_flags_a_character_valid_in_both_scripts(self) -> None:
        assert traditional_characters('乾坤未定，著作等身，於是动身', '') == []

    def test_exempts_characters_from_the_source(self) -> None:
        assert traditional_characters('请把「臺北」改成「台北」', '原文写作臺北') == []
