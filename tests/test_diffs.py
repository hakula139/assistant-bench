from pathlib import Path

import pytest

from assistant_bench.diffs import workspace_diff


def write_tree(root: Path, files: dict[str, bytes]) -> None:
    for path, content in files.items():
        file = root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(content)


class TestWorkspaceDiff:
    @pytest.mark.parametrize(
        ('before', 'after', 'expected'),
        [
            pytest.param(
                {'app.py': b'a\nb\nc\nd\ne\nf\ng\nh\n'},
                {'app.py': b'a\nb\nc\nd\nE\nf\ng\nh\n'},
                '--- a/app.py\n+++ b/app.py\n@@ -2,7 +2,7 @@\n b\n c\n d\n-e\n+E\n'
                ' f\n g\n h\n',
                id='modified-with-three-lines-of-context',
            ),
            pytest.param(
                {},
                {'new.py': b'x = 1\n'},
                '--- /dev/null\n+++ b/new.py\n@@ -0,0 +1 @@\n+x = 1\n',
                id='added',
            ),
            pytest.param(
                {'old.py': b'x = 1\n'},
                {},
                '--- a/old.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-x = 1\n',
                id='deleted',
            ),
            pytest.param(
                {'logo.png': b'\x89PNG\x00'},
                {'logo.png': b'\x89PNG\x01'},
                'Binary files a/logo.png and b/logo.png differ\n',
                id='binary',
            ),
            pytest.param(
                {},
                {'blob': b'\xff'},
                'Binary files /dev/null and b/blob differ\n',
                id='added-binary',
            ),
            pytest.param(
                {},
                {'greeting.txt': b'hello'},
                '--- /dev/null\n+++ b/greeting.txt\n@@ -0,0 +1 @@\n+hello\n'
                '\\ No newline at end of file\n',
                id='no-final-newline',
            ),
            pytest.param(
                {},
                {'__init__.py': b''},
                '--- /dev/null\n+++ b/__init__.py\n',
                id='added-empty-file',
            ),
            pytest.param(
                {'same.py': b'x\n', '.git/HEAD': b'ref: main\n'},
                {'same.py': b'x\n', '.git/HEAD': b'ref: other\n', 'pkg/.git/x': b'y'},
                '',
                id='unchanged-and-git-left-out',
            ),
            pytest.param(
                {},
                {'b.py': b'b\n', 'a/z.py': b'z\n'},
                '--- /dev/null\n+++ b/a/z.py\n@@ -0,0 +1 @@\n+z\n'
                '--- /dev/null\n+++ b/b.py\n@@ -0,0 +1 @@\n+b\n',
                id='sorted-paths',
            ),
        ],
    )
    def test_diffs_the_workspace_against_its_base(
        self,
        tmp_path: Path,
        before: dict[str, bytes],
        after: dict[str, bytes],
        expected: str,
    ) -> None:
        write_tree(tmp_path / 'base', before)
        write_tree(tmp_path / 'workspace', after)

        diff = workspace_diff(tmp_path / 'base', tmp_path / 'workspace')

        assert diff == expected

    def test_compares_a_symlink_by_its_target(self, tmp_path: Path) -> None:
        secret = tmp_path / 'secret'
        secret.write_text('do not leak\n')
        workspace = tmp_path / 'workspace'
        workspace.mkdir()
        (workspace / 'link').symlink_to(secret)
        (workspace / 'linked-dir').symlink_to(tmp_path)

        diff = workspace_diff(tmp_path / 'base', workspace)

        assert 'do not leak' not in diff
        assert diff == (
            f'--- /dev/null\n+++ b/link\n@@ -0,0 +1 @@\n+{secret}\n'
            '\\ No newline at end of file\n'
            f'--- /dev/null\n+++ b/linked-dir\n@@ -0,0 +1 @@\n+{tmp_path}\n'
            '\\ No newline at end of file\n'
        )
