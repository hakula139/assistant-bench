import pipeline

from .project import ProjectCase


class ExistingBehaviorTest(ProjectCase):
    def test_order_follows_the_files_steps_exchange(self) -> None:
        result = self.cli('order')

        self.assertEqual(result.stdout.split(), ['clean', 'report', 'publish'])

    def test_python_api_reports_ran_and_skipped_steps(self) -> None:
        messages: list[str] = []
        project = pipeline.load(self.root)

        first = pipeline.build(project, log=messages.append)
        second = pipeline.build(project, 'report', log=messages.append)

        self.assertEqual(
            (first.ran, first.skipped), (['clean', 'report', 'publish'], [])
        )
        self.assertEqual((second.ran, second.skipped), ([], ['clean', 'report']))
        self.assertEqual(messages[:3], ['ran clean', 'ran report', 'ran publish'])

    def test_steps_must_write_their_outputs(self) -> None:
        steps = self.root / 'steps.py'
        source = steps.read_text()
        steps.write_text(
            source.replace(
                "context.outputs[1].write_text(json.dumps({'rows': len(rows)}))", 'pass'
            )
        )

        failed = self.cli('build')

        self.assertEqual(failed.returncode, 1)
        self.assertIn('summary.json', failed.stderr)
        steps.write_text(source)
        self.assertEqual(self.build()[-2:], ['report', 'publish'])
        self.assert_matches_clean_build()

    def test_rejects_cycles(self) -> None:
        config = self.root / 'pipeline.toml'
        config.write_text(
            config.read_text().replace(
                'inputs = ["raw/*.csv"]', 'inputs = ["site/*.txt"]'
            )
        )

        with self.assertRaises(pipeline.ConfigError):
            pipeline.build(pipeline.load(self.root), log=lambda _: None)
