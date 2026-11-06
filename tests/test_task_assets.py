import subprocess
from pathlib import Path

import pytest

from assistant_bench import task_assets


SHARED_FILES = (
    'environment/Dockerfile',
    'solution/solve.sh',
    'tests/test.sh',
    'tests/verify.py',
)


def git(root: Path, *args: str) -> None:
    subprocess.run(['git', *args], cwd=root, check=True)


def shared_tree(root: Path) -> tuple[Path, list[Path]]:
    """Commit the shared files and two tasks in a new repository at `root`."""
    coding = root / 'families' / 'coding-repo'
    for relative in SHARED_FILES:
        source = coding / 'shared' / relative
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(f'canonical {relative}\n')

    tasks = [coding / 'tasks' / name for name in ('a', 'b')]
    for task in tasks:
        task.mkdir(parents=True)
        (task / 'task.toml').write_text(f'name = "{task.name}"\n')

    (root / '.gitignore').write_text('.DS_Store\n__pycache__/\n')
    git(root, 'init', '--quiet')
    git(root, 'config', 'user.email', 'test@example.com')
    git(root, 'config', 'user.name', 'Test')
    git(root, 'add', '.')
    git(root, 'commit', '--quiet', '-m', 'Shared files')
    return coding, tasks


def snapshot(root: Path) -> dict[Path, bytes]:
    return {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob('*')
        if path.is_file() and '.git' not in path.relative_to(root).parts
    }


class TestSharedFiles:
    def test_lists_each_file_git_does_not_ignore(self, tmp_path: Path) -> None:
        coding, _ = shared_tree(tmp_path)
        nested = coding / 'shared' / 'tests' / 'nested' / 'helper.py'
        nested.parent.mkdir()
        nested.write_text('helper\n')
        cache = coding / 'shared' / 'tests' / '__pycache__' / 'verify.cpython-314.pyc'
        cache.parent.mkdir()
        cache.write_bytes(b'bytecode')
        (coding / 'shared' / 'environment' / '.DS_Store').write_bytes(b'finder')
        (coding / 'shared' / 'solution' / 'solve.sh').unlink()

        files = task_assets.shared_files(tmp_path)

        assert files == sorted(
            Path(relative)
            for relative in {*SHARED_FILES, 'tests/nested/helper.py'}
            - {'solution/solve.sh'}
        )


class TestSync:
    def test_writes_each_missing_or_stale_copy_and_is_idempotent(
        self, tmp_path: Path
    ) -> None:
        coding, tasks = shared_tree(tmp_path)
        current = tasks[0] / SHARED_FILES[0]
        current.parent.mkdir()
        current.write_bytes((coding / 'shared' / SHARED_FILES[0]).read_bytes())
        stamp = current.stat().st_mtime_ns
        unrelated = coding / 'tasks' / 'notes' / 'README.md'
        unrelated.parent.mkdir()
        unrelated.write_text('not a task\n')
        before = snapshot(tmp_path)
        stale = tasks[1] / SHARED_FILES[0]
        stale.parent.mkdir()
        stale.write_bytes(b'old\n')

        updated, removed = task_assets.sync(tmp_path)

        expected = {task / shared for task in tasks for shared in SHARED_FILES}
        assert set(updated) == expected - {current}
        assert removed == []
        assert current.stat().st_mtime_ns == stamp
        for path in expected:
            relative = path.relative_to(path.parents[1])

            assert path.read_bytes() == (coding / 'shared' / relative).read_bytes()
        assert {
            path: value for path, value in snapshot(tmp_path).items() if path in before
        } == before

        assert task_assets.sync(tmp_path) == ([], [])

    def test_removes_the_copies_of_a_file_removed_from_shared(
        self, tmp_path: Path
    ) -> None:
        coding, tasks = shared_tree(tmp_path)
        task_assets.sync(tmp_path)
        (coding / 'shared' / 'tests' / 'verify.py').unlink()
        hidden = tasks[0] / 'tests' / 'hidden' / 'test_app.py'
        hidden.parent.mkdir()
        hidden.write_text('hidden\n')

        updated, removed = task_assets.sync(tmp_path)

        assert updated == []
        assert removed == [task / 'tests' / 'verify.py' for task in tasks]
        assert not any(path.exists() for path in removed)
        assert hidden.is_file()
        assert (tasks[0] / 'tests' / 'test.sh').is_file()


class TestMain:
    def test_reports_each_updated_and_removed_copy(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        coding, tasks = shared_tree(tmp_path)
        task_assets.sync(tmp_path)
        (coding / 'shared' / 'tests' / 'verify.py').unlink()
        (coding / 'shared' / 'tests' / 'test.sh').write_text('changed\n')
        monkeypatch.setattr(task_assets, 'REPO_ROOT', tmp_path)
        monkeypatch.setattr('sys.argv', ['task_assets'])

        task_assets.main()

        assert capsys.readouterr().out.splitlines() == [
            *(
                'Updated shared file '
                f'`{(task / "tests/test.sh").relative_to(tmp_path)}`.'
                for task in tasks
            ),
            *(
                'Removed stale shared file '
                f'`{(task / "tests/verify.py").relative_to(tmp_path)}`.'
                for task in tasks
            ),
        ]
