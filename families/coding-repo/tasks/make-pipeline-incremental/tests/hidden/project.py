"""A sample project that the hidden tests build through the command line."""

import dataclasses
import textwrap
import unittest
from typing import Any

from sandbox import observe, scratch


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


@dataclasses.dataclass(frozen=True)
class Completed:
    """A command-line run, with the steps it ran in order."""

    returncode: int
    stdout: str
    stderr: str
    runs: list[str]


class ProjectCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = str(scratch() / 'project')
        self.write_project()

    def write_project(self) -> None:
        files = {'steps.py': STEPS, 'pipeline.toml': CONFIG, **SOURCES}
        self.project('write', files=files)

    def project(self, function: str, /, **args: Any) -> Any:
        """Edit or build this test's project through `scenarios.project`."""
        return observe('project', function, root=self.root, **args)

    def api(self, function: str, /, **args: Any) -> Any:
        """Build this test's project through `scenarios.api`."""
        return observe('api', function, root=self.root, **args)

    def write(self, name: str, content: str) -> None:
        self.project('write', files={name: content})

    def read(self, name: str) -> str:
        return self.project('read', name=name)

    def cli(self, *args: str, env: dict[str, str] | None = None) -> Completed:
        """Run the command line, starting a new `runs.log`."""
        return Completed(**self.project('cli', args=args, env=env or {}))

    def build(self, *args: str, env: dict[str, str] | None = None) -> list[str]:
        """Build successfully and return the steps that ran, in order."""
        result = self.cli('build', *args, env=env)

        self.assertEqual(result.returncode, 0, result.stderr)
        return result.runs

    def edit(self, name: str, old: str, new: str) -> None:
        self.assertIn(old, self.project('edit', name=name, old=old, new=new))

    def assert_matches_clean_build(self) -> None:
        """Compare the outputs with those of a fresh copy of the current sources."""
        build = self.project('clean_build')

        self.assertEqual(build['returncode'], 0, build['stderr'])
        self.assertEqual(build['outputs'], build['fresh'])
