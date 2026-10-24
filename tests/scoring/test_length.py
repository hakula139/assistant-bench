import pytest

from assistant_bench.scoring.length import text_length


class TestTextLength:
    @pytest.mark.parametrize(
        ('text', 'length'),
        [
            ('Two words.', 2),
            ('用一句话欢迎。', 7),
            ('运行 nix flake check 即可', 7),
            ('增长一〇〇%', 6),
            ('', 0),
        ],
        ids=['words', 'han', 'mixed', 'ling', 'empty'],
    )
    def test_counts_han_characters_and_other_words(
        self, text: str, length: int
    ) -> None:
        assert text_length(text) == length
