"""A sample project that the hidden tests build through the command line."""

import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


STEPS = textwrap.dedent(
    """
    import json
    import os

    SEPARATOR = ','


    def _log(context, name):
        with open(context.root / 'runs.log', 'a') as log:
            log.write(name + '\\n')


    def clean(context):
        _log(context, 'clean')
        delimiter = context.params.get('delimiter', ',')
        rows = []
        for path in context.inputs:
            for line in path.read_text().splitlines():
                cells = [cell.strip() for cell in line.split(delimiter)]
                if line.strip():
                    rows.append(SEPARATOR.join(cells))
        context.outputs[0].write_text(''.join(row + '\\n' for row in sorted(rows)))


    def report(context):
        _log(context, 'report')
        table = next(path for path in context.inputs if path.suffix == '.csv')
        rows = table.read_text().splitlines()
        failure = os.environ.get('FAIL_REPORT')
        if failure:
            if failure != 'early':
                context.outputs[0].write_text('partial')
            raise RuntimeError('report failed')
        header = ''.join(
            path.read_text() for path in context.inputs if path.suffix == '.txt'
        )
        context.outputs[0].write_text(header + ''.join(f'- {row}\\n' for row in rows))
        context.outputs[1].write_text(json.dumps({'rows': len(rows)}))


    def publish(context):
        _log(context, 'publish')
        summary = json.loads(context.inputs[0].read_text())
        title = context.params['title']
        context.outputs[0].write_text(f"{title}: {summary['rows']} rows\\n")


    def shout(context):
        _log(context, 'shout')
        summary = json.loads(context.inputs[0].read_text())
        context.outputs[0].write_text(f"{summary['rows']} ROWS\\n")


    def pair(context):
        _log(context, 'pair')
        for output, text in zip(context.outputs, ('first', 'second')):
            output.write_text(text + '\\n')


    def index(context):
        _log(context, 'index')
        names = [path.relative_to(context.root).as_posix() for path in context.inputs]
        context.outputs[0].write_text(''.join(name + '\\n' for name in names))
    """
)

CONFIG = textwrap.dedent(
    """
    [steps.publish]
    run = "steps:publish"
    inputs = ["build/summary.json"]
    outputs = ["site/index.txt"]
    params = { title = "Weekly" }

    [steps.report]
    run = "steps:report"
    inputs = ["build/clean.csv", "templates/*.txt"]
    outputs = ["build/report.txt", "build/summary.json"]

    [steps.clean]
    run = "steps:clean"
    inputs = ["raw/*.csv"]
    outputs = ["build/clean.csv"]
    params = { delimiter = ";" }
    """
)

SOURCES = {
    'raw/a.csv': 'b; 2\na; 1\n',
    'raw/b.csv': 'c; 3\n',
    'templates/header.txt': 'Report\n',
}


class ProjectCase(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.root = self.workspace / 'project'
        self.write_project(self.root)

    def write_project(self, root: Path) -> None:
        root.mkdir()
        (root / 'steps.py').write_text(STEPS)
        (root / 'pipeline.toml').write_text(CONFIG)
        for name, content in SOURCES.items():
            self.write(name, content, root)

    def write(self, name: str, content: str, root: Path | None = None) -> None:
        path = (root or self.root) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def cli(
        self, *args: str, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Run the command line, starting a new `runs.log`."""
        (self.root / 'runs.log').unlink(missing_ok=True)
        return subprocess.run(
            [sys.executable, '-m', 'pipeline', '--project', str(self.root), *args],
            capture_output=True,
            text=True,
            env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', **(env or {})},
            timeout=60,
        )

    def build(self, *args: str, env: dict[str, str] | None = None) -> list[str]:
        """Build successfully and return the steps that ran, in order."""
        result = self.cli('build', *args, env=env)

        self.assertEqual(result.returncode, 0, result.stderr)
        return self.runs()

    def runs(self) -> list[str]:
        log = self.root / 'runs.log'
        return log.read_text().split() if log.exists() else []

    def outputs(self, root: Path) -> dict[str, bytes]:
        """Read every file under the output directories `build` and `site`."""
        return {
            path.relative_to(root).as_posix(): path.read_bytes()
            for directory in ('build', 'site')
            for path in sorted((root / directory).rglob('*'))
            if path.is_file()
        }

    def edit(self, name: str, old: str, new: str) -> None:
        path = self.root / name
        content = path.read_text()
        self.assertIn(old, content)
        path.write_text(content.replace(old, new))

    def assert_matches_clean_build(self) -> None:
        """Compare the outputs with those of a fresh copy of the current sources."""
        fresh = self.workspace / f'fresh-{len(list(self.workspace.iterdir()))}'
        shutil.copytree(
            self.root,
            fresh,
            ignore=lambda directory, names: [
                name
                for name in names
                if Path(directory) == self.root
                and (name.startswith('.') or name in {'build', 'site', 'runs.log'})
            ],
        )
        result = subprocess.run(
            [sys.executable, '-m', 'pipeline', '--project', str(fresh), 'build'],
            capture_output=True,
            text=True,
            env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'},
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.outputs(self.root), self.outputs(fresh))
