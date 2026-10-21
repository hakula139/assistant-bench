import textwrap

import pytest

from assistant_bench.families import Gates, VotesSettings
from assistant_bench.scoring.gates import (
    Gate,
    detect_language,
    failed_gates,
    traditional_characters,
)


ZH = VotesSettings(
    scorer='votes',
    samples=1,
    max_output_tokens=300,
    language='zh',
    gates=Gates(min_chars=10, max_chars=60, script='hans'),
)
EN = VotesSettings(
    scorer='votes', samples=1, max_output_tokens=300, language='en', gates=Gates()
)
ZH_REPLY = '我把 `ripgrep` 升到了 15.2.0，并更新了两个哈希。macOS 主机还没有构建。'
ZH_CODE = textwrap.dedent(
    """\
    ```python
    def fetch(client, cursor=None):
        # 接口限流为每秒一次，留 0.1 秒余量
        time.sleep(1.1)
        return client.get('/bars', params={'cursor': cursor})
    ```
    """
)


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
            ('  简短的回复，正好十个字  ', []),
            ('太短了。', [Gate.LENGTH]),
            ('长' * 60, []),
            ('长' * 61, [Gate.LENGTH]),
        ],
    )
    def test_bounds_the_stripped_length(self, text: str, failures: list[Gate]) -> None:
        assert failed_gates(ZH, text, 'stop', '') == failures

    def test_rejects_an_output_in_another_language(self) -> None:
        text = 'I bumped ripgrep to 15.2.0 and updated both hashes.'

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
    def test_ignores_inline_code_and_urls_in_chinese_prose(self) -> None:
        text = (
            '运行 `nix build .#packages.x86_64-linux.ripgrep --print-build-logs` 后'
            '构建通过，详见 https://github.com/BurntSushi/ripgrep/releases/tag/15.2.0'
        )

        assert detect_language(text) == 'zh'

    def test_reads_english_prose_quoting_chinese(self) -> None:
        text = 'The error message 文件不存在 means the file does not exist yet.'

        assert detect_language(text) == 'en'

    @pytest.mark.parametrize(
        ('comment', 'language'),
        [('接口限流为每秒一次，留 0.1 秒余量', 'zh'), ('Rate limit of 1/s', 'en')],
    )
    def test_judges_code_by_its_comments(self, comment: str, language: str) -> None:
        text = ZH_CODE.replace('接口限流为每秒一次，留 0.1 秒余量', comment)

        assert detect_language(text) == language

    def test_skips_shebangs_and_attributes(self) -> None:
        text = textwrap.dedent(
            """\
            ```rust
            #!/usr/bin/env rust-script
            #[derive(Debug)]
            struct Retry; // 重试
            ```
            """
        )

        assert detect_language(text) == 'zh'

    def test_reads_block_comments_and_docstrings(self) -> None:
        text = textwrap.dedent(
            '''\
            ```rust
            /* 缓冲区按页对齐 */
            fn main() { let buffer_length_in_bytes = 4096; }
            ```

            ```python
            def parse(raw_input_bytes):
                """解析原始字节。"""
            ```
            '''
        )

        assert detect_language(text) == 'zh'

    def test_returns_none_without_natural_language(self) -> None:
        assert detect_language('```\nx = 1\n```\n`y` 42') is None


class TestTraditionalCharacters:
    def test_lists_each_converted_character_once(self) -> None:
        assert traditional_characters('這個設定，這個測試', '') == [
            '這',
            '個',
            '設',
            '測',
            '試',
        ]

    def test_keeps_characters_simplified_in_context(self) -> None:
        assert traditional_characters('乾坤未定，显著提升', '') == []
        assert traditional_characters('房间很乾淨', '') == ['乾', '淨']

    def test_exempts_characters_from_the_source(self) -> None:
        assert traditional_characters('请把「臺北」改成「台北」', '原文写作臺北') == []
