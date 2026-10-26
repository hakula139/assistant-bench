from pathlib import Path

import pytest

from assistant_bench import task_assets


def shared_tree(root: Path) -> tuple[Path, list[Path]]:
    coding = root / 'families' / 'coding-repo'
    for relative in task_assets.SHARED_FILES:
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
    def test_writes_each_missing_or_stale_copy_and_is_idempotent(
        self, tmp_path: Path
    ) -> None:
        coding, tasks = shared_tree(tmp_path)
        current = tasks[0] / task_assets.SHARED_FILES[0]
        current.parent.mkdir()
        current.write_bytes(
            (coding / 'shared' / task_assets.SHARED_FILES[0]).read_bytes()
        )
        stamp = current.stat().st_mtime_ns
        unrelated = coding / 'tasks' / 'notes' / 'README.md'
        unrelated.parent.mkdir()
        unrelated.write_text('not a task\n')
        before = snapshot(tmp_path)

        updated = task_assets.sync(tmp_path)

        expected = {
            task / shared for task in tasks for shared in task_assets.SHARED_FILES
        }
        assert set(updated) == expected - {current}
        assert current.stat().st_mtime_ns == stamp
        for path in expected:
            relative = path.relative_to(path.parents[1])

            assert path.read_bytes() == (coding / 'shared' / relative).read_bytes()
        assert {
            path: value for path, value in snapshot(tmp_path).items() if path in before
        } == before

        assert task_assets.sync(tmp_path) == []


class TestMain:
    def test_reports_each_updated_copy(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _, tasks = shared_tree(tmp_path)
        monkeypatch.setattr(task_assets, 'REPO_ROOT', tmp_path)

        task_assets.main()

        assert set(capsys.readouterr().out.splitlines()) == {
            f'Updated shared file `{(task / shared).relative_to(tmp_path)}`.'
            for task in tasks
            for shared in task_assets.SHARED_FILES
        }
