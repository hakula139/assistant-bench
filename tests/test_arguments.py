import pytest
from typer.testing import CliRunner

from assistant_bench.cli import app
from assistant_bench.families import family_names


runner = CliRunner()


class TestKnownNames:
    @pytest.mark.parametrize(
        'command',
        [('check',), ('run',), ('collect',), ('close',), ('arena', 'prepare')],
        ids=['check', 'run', 'collect', 'close', 'arena-prepare'],
    )
    def test_rejects_a_missing_family_and_names_the_choices(
        self, command: tuple[str, ...]
    ) -> None:
        choices = ', '.join(f'`{name}`' for name in family_names())

        result = runner.invoke(app, [*command, 'nope'], env={'COLUMNS': '200'})

        assert result.exit_code == 2
        assert f'No family named `nope`. Choose among {choices}.' in result.output
