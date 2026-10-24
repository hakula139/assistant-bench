import sys
from pathlib import Path

import pytest

from assistant_bench import task_assets


ASSETS = ('environment/Dockerfile', 'tests/test.sh', 'tests/verify_lib.py')


def asset_tree(root: Path) -> tuple[Path, list[Path]]:
    coding = root / 'families' / 'coding-repo'
    for relative in ASSETS:
        source = coding / 'shared' / relative
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(f'canonical {relative}\n')
    tasks = [coding / 'tasks' / name for name in ('a', 'b')]
    for task in tasks:
        task.mkdir(parents=True)
        (task / 'task.toml').write_text(f'name = "{task.name}"\n')
    return coding, tasks


def snapshot(root: Path) -> dict[Path, bytes]:
    return {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob('*')
        if path.is_file()
    }


class TestSync:
    @pytest.mark.parametrize('target', ['matching', 'stale', 'broken'])
    def test_replaces_generated_links_without_writing_their_targets(
        self, tmp_path: Path, target: str
    ) -> None:
        repository = tmp_path / 'repo'
        coding, tasks = asset_tree(repository)
        task_assets.sync(repository)
        expected = (coding / 'shared' / ASSETS[-1]).read_bytes()
        outside = tmp_path / 'outside'
        outside.mkdir()
        external = outside / 'artifact.py'
        if target != 'broken':
            external.write_bytes(expected if target == 'matching' else b'external\n')

        leaf = tasks[0] / ASSETS[-1]
        leaf.unlink()
        leaf.symlink_to(external)
        before = snapshot(tmp_path)
        external_before = snapshot(outside)
        stamp = external.stat().st_mtime_ns if external.exists() else None

        stale = task_assets.sync(repository, check=True)

        assert stale == [leaf]
        assert leaf.is_symlink()
        assert leaf.readlink() == external
        assert snapshot(tmp_path) == before

        updated = task_assets.sync(repository)

        assert updated == [leaf]
        assert leaf.is_file() and not leaf.is_symlink()
        assert leaf.read_bytes() == expected
        assert snapshot(outside) == external_before
        if stamp is not None:
            assert external.stat().st_mtime_ns == stamp
            assert not leaf.samefile(external)

        assert task_assets.sync(repository) == []

    def test_check_reports_missing_and_stale_files_without_writing(
        self, tmp_path: Path
    ) -> None:
        coding, tasks = asset_tree(tmp_path)
        current = tasks[0] / ASSETS[0]
        current.parent.mkdir()
        current.write_bytes((coding / 'shared' / ASSETS[0]).read_bytes())
        stale = tasks[1] / ASSETS[0]
        stale.parent.mkdir()
        stale.write_text('old content\n')
        before = snapshot(tmp_path)

        changed = task_assets.sync(tmp_path, check=True)

        assert set(changed) == {
            task / relative for task in tasks for relative in ASSETS
        } - {current}
        assert snapshot(tmp_path) == before

    def test_regenerates_only_known_stale_assets_and_is_idempotent(
        self, tmp_path: Path
    ) -> None:
        coding, tasks = asset_tree(tmp_path)
        current = tasks[0] / ASSETS[0]
        current.parent.mkdir()
        current.write_bytes((coding / 'shared' / ASSETS[0]).read_bytes())
        stamp = current.stat().st_mtime_ns
        unrelated = coding / 'tasks' / 'notes' / 'README.md'
        unrelated.parent.mkdir()
        unrelated.write_text('not a task\n')
        before = snapshot(tmp_path)

        changed = task_assets.sync(tmp_path)

        expected = {task / relative for task in tasks for relative in ASSETS}
        assert set(changed) == expected - {current}
        assert current.stat().st_mtime_ns == stamp
        for path in expected:
            relative = path.relative_to(path.parents[1])
            assert path.read_bytes() == (coding / 'shared' / relative).read_bytes()
        assert {
            path: value for path, value in snapshot(tmp_path).items() if path in before
        } == before
        after = {
            path: (path.read_bytes(), path.stat().st_mtime_ns) for path in expected
        }

        assert task_assets.sync(tmp_path) == []
        assert {
            path: (path.read_bytes(), path.stat().st_mtime_ns) for path in expected
        } == after


class TestMain:
    @pytest.mark.parametrize('check', [False, True], ids=['regenerate', 'check'])
    def test_reports_stale_paths_and_check_does_not_write(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        check: bool,
    ) -> None:
        _, tasks = asset_tree(tmp_path)
        monkeypatch.setattr(task_assets, 'REPO_ROOT', tmp_path)
        monkeypatch.setattr(
            sys, 'argv', ['task-assets', *(['--check'] if check else [])]
        )
        before = snapshot(tmp_path)

        if check:
            with pytest.raises(SystemExit) as error:
                task_assets.main()
            assert error.value.code == 1
        else:
            task_assets.main()

        status = 'Stale' if check else 'Updated'
        assert set(capsys.readouterr().out.splitlines()) == {
            f'{status} artifact `{(task / relative).relative_to(tmp_path)}`.'
            for task in tasks
            for relative in ASSETS
        }
        if check:
            assert snapshot(tmp_path) == before
