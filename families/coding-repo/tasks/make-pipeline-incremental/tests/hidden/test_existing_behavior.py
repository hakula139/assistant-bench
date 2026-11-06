from .project import ProjectCase


class ExistingBehaviorTest(ProjectCase):
    def test_order_follows_the_files_steps_exchange(self) -> None:
        result = self.cli('order')

        self.assertEqual(result.stdout.split(), ['clean', 'report', 'publish'])

    def test_python_api_reports_ran_and_skipped_steps(self) -> None:
        builds = self.api('builds', target='report')

        first, second = builds['first'], builds['second']
        self.assertEqual(
            (first['ran'], first['skipped']), (['clean', 'report', 'publish'], [])
        )
        self.assertEqual((second['ran'], second['skipped']), ([], ['clean', 'report']))
        self.assertEqual(
            builds['messages'][:3], ['ran clean', 'ran report', 'ran publish']
        )

    def test_steps_must_write_their_outputs(self) -> None:
        source = self.project(
            'edit',
            name='steps.py',
            old="context.outputs[1].write_text(json.dumps({'rows': len(rows)}))",
            new='pass',
        )

        failed = self.cli('build')

        self.assertEqual(failed.returncode, 1)
        self.assertIn('summary.json', failed.stderr)
        self.write('steps.py', source)
        self.assertEqual(self.build()[-2:], ['report', 'publish'])
        self.assert_matches_clean_build()

    def test_rejects_cycles(self) -> None:
        self.project(
            'edit',
            name='pipeline.toml',
            old='inputs = ["raw/*.csv"]',
            new='inputs = ["site/*.txt"]',
        )

        rejected = self.api('rejects_config')

        self.assertIs(rejected, True)
