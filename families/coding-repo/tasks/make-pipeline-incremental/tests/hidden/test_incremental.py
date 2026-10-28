import os

import pipeline

from .project import ProjectCase


ALL = ['clean', 'report', 'publish']

PAIR = """
[steps.pair]
run = "steps:pair"
outputs = ["build/first.txt", "build/second.txt"]
"""

INDEX = """
[steps.index]
run = "steps:index"
inputs = ["**/*.json"]
outputs = ["site/files.txt"]
"""


class IncrementalBuildTest(ProjectCase):
    def setUp(self) -> None:
        super().setUp()
        self.assertEqual(self.build(), ALL)

    def test_unchanged_projects_run_nothing(self) -> None:
        self.assertEqual(self.build(), [])

        result = self.cli('build')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('ran ', result.stdout)
        self.assert_matches_clean_build()

    def test_touching_files_runs_nothing(self) -> None:
        for name in [*self.root.glob('raw/*'), self.root / 'steps.py']:
            os.utime(name, (2_000_000_000, 2_000_000_000))

        self.assertEqual(self.build(), [])

    def test_edits_that_keep_size_and_time_are_seen(self) -> None:
        path = self.root / 'raw/b.csv'
        status = path.stat()
        path.write_text('c; 4\n')
        os.utime(path, ns=(status.st_atime_ns, status.st_mtime_ns))

        self.assertEqual(self.build(), ['clean', 'report'])
        self.assert_matches_clean_build()

    def test_unchanged_intermediate_outputs_stop_reruns(self) -> None:
        for content in ('a; 1\nb; 2\n', 'b;2\n\na ;1\n'):
            with self.subTest(content=content):
                self.write('raw/a.csv', content)

                self.assertEqual(self.build(), ['clean'])
                self.assert_matches_clean_build()

    def test_glob_matches_that_appear_or_vanish(self) -> None:
        self.write('raw/c.csv', 'd; 4\n')
        self.assertEqual(self.build(), ALL)

        (self.root / 'raw/b.csv').unlink()
        self.assertEqual(self.build(), ALL)

        self.write('raw/notes.md', 'not an input')
        self.assertEqual(self.build(), [])
        self.assert_matches_clean_build()

    def test_parameter_changes(self) -> None:
        self.edit('pipeline.toml', '"Weekly"', '"Daily"')

        self.assertEqual(self.build(), ['publish'])
        self.assertEqual((self.root / 'site/index.txt').read_text(), 'Daily: 3 rows\n')

    def test_parameter_type_changes(self) -> None:
        for old, new in (('"Weekly"', '2025-01-01'), ('2025-01-01', '"2025-01-01"')):
            with self.subTest(title=new):
                self.edit('pipeline.toml', f'title = {old}', f'title = {new}')

                self.assertEqual(self.build(), ['publish'])
                self.assert_matches_clean_build()

    def test_pattern_changes_with_the_same_matches(self) -> None:
        self.edit('pipeline.toml', '["raw/*.csv"]', '["raw/*.csv", "raw/*.tsv"]')

        self.assertEqual(self.build(), ['clean'])
        self.assert_matches_clean_build()

    def test_run_changes_within_one_module(self) -> None:
        self.edit('pipeline.toml', '"steps:publish"', '"steps:shout"')

        self.assertEqual(self.build(), ['shout'])
        self.assert_matches_clean_build()

    def test_reordered_outputs(self) -> None:
        config = self.root / 'pipeline.toml'
        config.write_text(config.read_text() + PAIR)
        self.assertEqual(self.build(), ['pair'])

        self.edit(
            'pipeline.toml',
            '["build/first.txt", "build/second.txt"]',
            '["build/second.txt", "build/first.txt"]',
        )

        self.assertEqual(self.build(), ['pair'])
        self.assertEqual((self.root / 'build/second.txt').read_text(), 'first\n')
        self.assert_matches_clean_build()

    def test_recursive_patterns_see_only_project_files(self) -> None:
        config = self.root / 'pipeline.toml'
        config.write_text(config.read_text() + INDEX)
        self.assertEqual(self.build(), ['index'])

        self.assertEqual(self.build(), [])
        self.assertEqual(
            (self.root / 'site/files.txt').read_text(), 'build/summary.json\n'
        )
        self.assert_matches_clean_build()

    def test_module_source_changes(self) -> None:
        edits = [
            ("SEPARATOR = ','", "SEPARATOR = ' | '"),
            ('rows\\n"', 'rows in total\\n"'),
            ('def _log(', '# Each step logs its runs.\ndef _log('),
        ]
        for old, new in edits:
            with self.subTest(edit=new):
                self.edit('steps.py', old, new)

                self.assertEqual(self.build(), ALL)
                self.assert_matches_clean_build()

    def test_reexported_functions_follow_their_defining_module(self) -> None:
        self.write('facade.py', 'from steps import publish as published\n')
        self.edit('pipeline.toml', '"steps:publish"', '"facade:published"')
        self.assertEqual(self.build(), ['publish'])

        self.edit('steps.py', 'rows\\n"', 'rows in total\\n"')

        self.assertEqual(self.build(), ALL)
        self.assert_matches_clean_build()

    def test_module_edits_between_builds_in_one_process(self) -> None:
        """Builds through the Python API, under a module name no other test imports."""
        steps = self.root / 'edited_steps.py'
        (self.root / 'steps.py').rename(steps)
        config = self.root / 'pipeline.toml'
        config.write_text(config.read_text().replace('"steps:', '"edited_steps:'))
        project = pipeline.load(self.root)
        pipeline.build(project, log=lambda _: None)
        steps.write_text(
            steps.read_text().replace("SEPARATOR = ','", "SEPARATOR = ' | '")
        )

        pipeline.build(project, log=lambda _: None)

        self.build()
        self.assert_matches_clean_build()

    def test_missing_or_edited_outputs_are_rebuilt(self) -> None:
        (self.root / 'build/summary.json').unlink()
        self.assertEqual(self.build(), ['report'])

        self.write('site/index.txt', 'edited by hand\n')
        self.assertEqual(self.build(), ['publish'])
        self.assert_matches_clean_build()

    def test_failed_steps_run_again(self) -> None:
        self.write('raw/c.csv', 'd; 4\n')

        failed = self.cli('build', env={'FAIL_REPORT': '1'})

        self.assertEqual(failed.returncode, 1)
        self.assertIn('report failed', failed.stderr)
        self.assertEqual(self.runs(), ['clean', 'report'])
        self.assertEqual(self.build(), ['report', 'publish'])
        self.assert_matches_clean_build()

    def test_partial_outputs_of_failed_runs_are_replaced(self) -> None:
        original = (self.root / 'raw/b.csv').read_text()
        self.write('raw/b.csv', 'c; 30\n')
        self.assertEqual(self.cli('build', env={'FAIL_REPORT': '1'}).returncode, 1)

        self.write('raw/b.csv', original)

        self.assertEqual(self.build(), ['clean', 'report'])
        self.assert_matches_clean_build()

    def test_targets_record_what_they_build(self) -> None:
        self.write('raw/c.csv', 'd; 4\n')

        self.assertEqual(self.build('--target', 'clean'), ['clean'])
        self.assertEqual(self.build(), ['report', 'publish'])
        self.assert_matches_clean_build()

    def test_clean_starts_over(self) -> None:
        result = self.cli('clean')

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.build(), ALL)
        self.assert_matches_clean_build()
