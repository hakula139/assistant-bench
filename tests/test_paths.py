from pathlib import Path

import pytest

from assistant_bench.paths import DATA_DIR_VAR, data_dir


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
