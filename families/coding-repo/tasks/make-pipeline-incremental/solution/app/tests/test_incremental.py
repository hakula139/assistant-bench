import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


STEPS = textwrap.dedent(
    """
    import os

    def _log(context, name):
        with open(context.root / 'runs.log', 'a') as log:
            log.write(name + '\\n')

    def lower(context):
        _log(context, 'lower')
        text = ''.join(path.read_text() for path in context.inputs)
        context.outputs[0].write_text(text.lower())

    def count(context):
        _log(context, 'count')
        if os.environ.get('FAIL_COUNT'):
            context.outputs[0].write_text('partial')
            raise RuntimeError('count failed')
        words = context.inputs[0].read_text().split()
        context.outputs[0].write_text(f"{context.params['label']}: {len(words)}\\n")
    """
)

CONFIG = textwrap.dedent(
    """
    [steps.lower]
    run = "steps:lower"
    inputs = ["src/*.txt"]
    outputs = ["out/lower.txt"]

    [steps.count]
    run = "steps:count"
    inputs = ["out/lower.txt"]
    outputs = ["out/count.txt"]
    params = { label = "words" }
    """
)


class IncrementalTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (self.root / 'steps.py').write_text(STEPS)
        (self.root / 'pipeline.toml').write_text(CONFIG)
        (self.root / 'src').mkdir()
        (self.root / 'src/a.txt').write_text('One Two\n')
        self.assertEqual(self.build(), ['lower', 'count'])

    def build(self, **env: str) -> list[str]:
        log = self.root / 'runs.log'
        log.unlink(missing_ok=True)
        subprocess.run(
            [sys.executable, '-m', 'pipeline', '--project', str(self.root), 'build'],
            capture_output=True,
            env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', **env},
        )
        return log.read_text().split() if log.exists() else []

    def test_skips_steps_whose_inputs_kept_their_content(self) -> None:
        os.utime(self.root / 'src/a.txt', (2_000_000_000, 2_000_000_000))
        self.assertEqual(self.build(), [])

        (self.root / 'src/a.txt').write_text('ONE TWO\n')
        self.assertEqual(self.build(), ['lower'])

    def test_same_size_edits_at_the_same_time_rerun(self) -> None:
        path = self.root / 'src/a.txt'
        status = path.stat()
        path.write_text('One Six\n')
        os.utime(path, ns=(status.st_atime_ns, status.st_mtime_ns))

        self.assertEqual(self.build(), ['lower', 'count'])
        self.assertEqual((self.root / 'out/lower.txt').read_text(), 'one six\n')

    def test_new_glob_matches_and_parameters_rerun(self) -> None:
        (self.root / 'src/b.txt').write_text('three\n')
        self.assertEqual(self.build(), ['lower', 'count'])

        config = self.root / 'pipeline.toml'
        config.write_text(config.read_text().replace('"words"', '"tokens"'))
        self.assertEqual(self.build(), ['count'])

    def test_pattern_changes_rerun(self) -> None:
        config = self.root / 'pipeline.toml'
        config.write_text(
            config.read_text().replace('["src/*.txt"]', '["src/*.txt", "extra/*.txt"]')
        )

        self.assertEqual(self.build(), ['lower'])

    def test_rebuilds_missing_outputs_and_failed_steps(self) -> None:
        (self.root / 'out/count.txt').unlink()
        self.assertEqual(self.build(), ['count'])

        (self.root / 'src/a.txt').write_text('One Two Three\n')
        self.assertEqual(self.build(FAIL_COUNT='1'), ['lower', 'count'])
        (self.root / 'src/a.txt').write_text('One Two\n')

        self.assertEqual(self.build(), ['lower', 'count'])
        self.assertEqual((self.root / 'out/count.txt').read_text(), 'words: 2\n')

    def test_module_source_changes_rerun(self) -> None:
        steps = self.root / 'steps.py'
        steps.write_text(steps.read_text().replace('text.lower()', 'text.casefold()'))

        self.assertEqual(self.build(), ['lower', 'count'])
