import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import pipeline


STEPS = textwrap.dedent(
    """
    def _log(context, name):
        with open(context.root / 'runs.log', 'a') as log:
            log.write(name + '\\n')

    def upper(context):
        _log(context, 'upper')
        text = ''.join(path.read_text() for path in context.inputs)
        context.outputs[0].write_text(text.upper())

    def count(context):
        _log(context, 'count')
        words = context.inputs[0].read_text().split()
        context.outputs[0].write_text(f"{context.params['label']}: {len(words)}\\n")

    def broken(context):
        raise ValueError('no luck')
    """
)

CONFIG = textwrap.dedent(
    """
    [steps.count]
    run = "steps:count"
    inputs = ["out/upper.txt"]
    outputs = ["out/count.txt"]
    params = { label = "words" }

    [steps.upper]
    run = "steps:upper"
    inputs = ["src/*.txt"]
    outputs = ["out/upper.txt"]
    """
)


class BuildTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (self.root / 'steps.py').write_text(STEPS)
        (self.root / 'pipeline.toml').write_text(CONFIG)
        (self.root / 'src').mkdir()
        (self.root / 'src' / 'a.txt').write_text('one two\n')
        (self.root / 'src' / 'b.txt').write_text('three\n')

    def cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, '-m', 'pipeline', '--project', str(self.root), *args],
            capture_output=True,
            text=True,
        )

    def runs(self) -> list[str]:
        return (self.root / 'runs.log').read_text().split()

    def test_builds_steps_in_dependency_order(self) -> None:
        result = self.cli('build')

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.runs(), ['upper', 'count'])
        self.assertEqual((self.root / 'out/upper.txt').read_text(), 'ONE TWO\nTHREE\n')
        self.assertEqual((self.root / 'out/count.txt').read_text(), 'words: 3\n')

    def test_target_builds_only_its_dependencies(self) -> None:
        result = self.cli('build', '--target', 'upper')

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.runs(), ['upper'])
        self.assertFalse((self.root / 'out/count.txt').exists())

    def test_order_and_clean(self) -> None:
        self.assertEqual(self.cli('order').stdout.split(), ['upper', 'count'])
        self.cli('build')

        self.cli('clean')

        self.assertEqual(list((self.root / 'out').iterdir()), [])

    def test_failed_steps_stop_the_build(self) -> None:
        (self.root / 'pipeline.toml').write_text(
            CONFIG.replace('run = "steps:upper"', 'run = "steps:broken"')
        )

        result = self.cli('build')

        self.assertEqual(result.returncode, 1)
        self.assertIn('no luck', result.stderr)
        self.assertFalse((self.root / 'out/count.txt').exists())

    def test_rejects_cycles_and_shared_outputs(self) -> None:
        cases = [
            CONFIG.replace('inputs = ["src/*.txt"]', 'inputs = ["out/count.txt"]'),
            CONFIG.replace(
                'outputs = ["out/count.txt"]', 'outputs = ["out/upper.txt"]'
            ),
        ]
        for config in cases:
            with self.subTest(config=config):
                (self.root / 'pipeline.toml').write_text(config)

                with self.assertRaises(pipeline.ConfigError):
                    pipeline.build(pipeline.load(self.root), log=lambda _: None)
