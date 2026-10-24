from pathlib import Path

import pytest

from assistant_bench.diffs import workspace_diff


LINE_SEPARATOR = chr(0x2028)


def write_tree(root: Path, files: dict[str, bytes]) -> None:
    for path, content in files.items():
        file = root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(content)


def header(path: str, mode: str = '') -> str:
    return f'diff --git a/{path} b/{path}\n{mode}'


class TestWorkspaceDiff:
    @pytest.mark.parametrize(
        ('before', 'after', 'expected'),
        [
            pytest.param(
                {'app.py': b'a\nb\nc\nd\ne\nf\ng\nh\n'},
                {'app.py': b'a\nb\nc\nd\nE\nf\ng\nh\n'},
                header('app.py') + '--- a/app.py\n+++ b/app.py\n'
                '@@ -2,7 +2,7 @@\n b\n c\n d\n-e\n+E\n f\n g\n h\n',
                id='modified-with-three-lines-of-context',
            ),
            pytest.param(
                {},
                {'new.py': b'x = 1\n'},
                header('new.py', 'new file mode 100644\n')
                + '--- /dev/null\n+++ b/new.py\n@@ -0,0 +1 @@\n+x = 1\n',
                id='added',
            ),
            pytest.param(
                {'old.py': b'x = 1\n'},
                {},
                header('old.py', 'deleted file mode 100644\n')
                + '--- a/old.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-x = 1\n',
                id='deleted',
            ),
            pytest.param(
                {'logo.png': b'\x89PNG\x00'},
                {'logo.png': b'\x89PNG\x01'},
                header('logo.png') + 'Binary files a/logo.png and b/logo.png differ\n',
                id='binary',
            ),
            pytest.param(
                {'data.bin': b'a\x00b\n'},
                {'data.bin': b'a\x00c\n'},
                header('data.bin') + 'Binary files a/data.bin and b/data.bin differ\n',
                id='utf-8-with-nul-is-binary',
            ),
            pytest.param(
                {},
                {'blob': b'\xff'},
                header('blob', 'new file mode 100644\n')
                + 'Binary files /dev/null and b/blob differ\n',
                id='added-binary',
            ),
            pytest.param(
                {},
                {'greeting.txt': b'hello'},
                header('greeting.txt', 'new file mode 100644\n')
                + '--- /dev/null\n+++ b/greeting.txt\n@@ -0,0 +1 @@\n+hello\n'
                '\\ No newline at end of file\n',
                id='no-final-newline',
            ),
            pytest.param(
                {'m.py': b'a\n\x0c\nb\n'},
                {'m.py': b'a\n\x0c\nB\n'},
                header('m.py') + '--- a/m.py\n+++ b/m.py\n'
                '@@ -1,3 +1,3 @@\n a\n \x0c\n-b\n+B\n',
                id='form-feed-stays-in-its-line',
            ),
            pytest.param(
                {'s.js': f"x = '{LINE_SEPARATOR}';\r\n".encode()},
                {'s.js': f"y = '{LINE_SEPARATOR}';\r\n".encode()},
                header('s.js') + '--- a/s.js\n+++ b/s.js\n'
                f"@@ -1 +1 @@\n-x = '{LINE_SEPARATOR}';\r\n"
                f"+y = '{LINE_SEPARATOR}';\r\n",
                id='line-separator-and-crlf-stay-in-their-line',
            ),
            pytest.param(
                {},
                {'__init__.py': b''},
                header('__init__.py', 'new file mode 100644\n'),
                id='added-empty-file',
            ),
            pytest.param(
                {'same.py': b'x\n', '.git/HEAD': b'ref: main\n', '.gitignore': b'a\n'},
                {
                    'same.py': b'x\n',
                    '.git/HEAD': b'ref: other\n',
                    'pkg/.git/x': b'y',
                    '.gitignore': b'b\n',
                    '.github/ci.yml': b'on: push\n',
                },
                header('.github/ci.yml', 'new file mode 100644\n')
                + '--- /dev/null\n+++ b/.github/ci.yml\n@@ -0,0 +1 @@\n+on: push\n'
                + header('.gitignore')
                + '--- a/.gitignore\n+++ b/.gitignore\n@@ -1 +1 @@\n-a\n+b\n',
                id='git-directories-left-out',
            ),
            pytest.param(
                {'app.py': b'x\n'},
                {
                    'app.py': b'x\n',
                    'pkg/__pycache__/app.cpython-313.pyc': b'\x00',
                    '.pytest_cache/v/cache/lastfailed': b'{"t": true}\n',
                    '.mypy_cache/3.13/app.meta.json': b'{}\n',
                    '.ruff_cache/CACHEDIR.TAG': b'tag\n',
                    'pycache/keep.py': b'k\n',
                },
                header('pycache/keep.py', 'new file mode 100644\n')
                + '--- /dev/null\n+++ b/pycache/keep.py\n@@ -0,0 +1 @@\n+k\n',
                id='tool-caches-left-out',
            ),
            pytest.param(
                {},
                {'b.py': b'b\n', 'a/z.py': b'z\n'},
                header('a/z.py', 'new file mode 100644\n')
                + '--- /dev/null\n+++ b/a/z.py\n@@ -0,0 +1 @@\n+z\n'
                + header('b.py', 'new file mode 100644\n')
                + '--- /dev/null\n+++ b/b.py\n@@ -0,0 +1 @@\n+b\n',
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
            header('link', 'new file mode 120000\n')
            + f'--- /dev/null\n+++ b/link\n@@ -0,0 +1 @@\n+{secret}\n'
            '\\ No newline at end of file\n'
            + header('linked-dir', 'new file mode 120000\n')
            + f'--- /dev/null\n+++ b/linked-dir\n@@ -0,0 +1 @@\n+{tmp_path}\n'
            '\\ No newline at end of file\n'
        )

    def test_shows_a_mode_change_and_a_symlink_swapped_for_its_target_bytes(
        self, tmp_path: Path
    ) -> None:
        write_tree(tmp_path / 'base', {'run.sh': b'echo\n'})
        write_tree(tmp_path / 'workspace', {'run.sh': b'echo\n', 'link': b'target'})
        (tmp_path / 'base' / 'link').symlink_to('target')
        (tmp_path / 'workspace' / 'run.sh').chmod(0o755)

        diff = workspace_diff(tmp_path / 'base', tmp_path / 'workspace')

        assert diff == (
            header('link', 'old mode 120000\nnew mode 100644\n')
            + header('run.sh', 'old mode 100644\nnew mode 100755\n')
        )
