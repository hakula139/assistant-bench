from pathlib import Path

import pytest

from assistant_bench.paths import DATA_DIR_VAR, data_dir, write_atomically


class TestDataDir:
    def test_prefers_the_bench_variable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(DATA_DIR_VAR, str(tmp_path / 'scratch'))
        monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'xdg'))

        path = data_dir()

        assert path == tmp_path / 'scratch'

    def test_falls_back_to_xdg_data_home(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(DATA_DIR_VAR)
        monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'xdg'))

        path = data_dir()

        assert path == tmp_path / 'xdg' / 'assistant-bench'

    def test_defaults_to_the_home_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(DATA_DIR_VAR)
        monkeypatch.delenv('XDG_DATA_HOME', raising=False)
        monkeypatch.setenv('HOME', str(tmp_path))

        path = data_dir()

        assert path == tmp_path / '.local' / 'share' / 'assistant-bench'


class TestWriteAtomically:
    def test_keeps_the_old_file_when_the_write_fails(self, tmp_path: Path) -> None:
        path = tmp_path / 'f.jsonl'
        path.write_text('old\n')

        with pytest.raises(UnicodeEncodeError):
            write_atomically(path, 'new\n\ud800')

        assert path.read_text() == 'old\n'
        assert list(tmp_path.iterdir()) == [path]
