from pathlib import Path

import pytest

from assistant_bench.paths import DATA_DIR_VAR, data_dir, write_atomically


class TestDataDir:
    @pytest.mark.parametrize(
        ('env', 'expected'),
        [
            ({DATA_DIR_VAR: 'scratch', 'XDG_DATA_HOME': 'xdg'}, 'scratch'),
            ({'XDG_DATA_HOME': 'xdg'}, 'xdg/assistant-bench'),
            ({}, '.local/share/assistant-bench'),
        ],
        ids=['bench-variable', 'xdg-data-home', 'home-directory'],
    )
    def test_prefers_the_bench_variable_then_xdg_data_home_then_home(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        env: dict[str, str],
        expected: str,
    ) -> None:
        monkeypatch.delenv(DATA_DIR_VAR, raising=False)
        monkeypatch.delenv('XDG_DATA_HOME', raising=False)
        monkeypatch.setenv('HOME', str(tmp_path))
        for name, value in env.items():
            monkeypatch.setenv(name, str(tmp_path / value))

        path = data_dir()

        assert path == tmp_path / expected


class TestWriteAtomically:
    def test_keeps_the_old_file_when_the_write_fails(self, tmp_path: Path) -> None:
        path = tmp_path / 'f.jsonl'
        path.write_text('old\n')

        with pytest.raises(UnicodeEncodeError):
            write_atomically(path, 'new\n\ud800')

        assert path.read_text() == 'old\n'
        assert list(tmp_path.iterdir()) == [path]
