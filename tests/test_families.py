import json
import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from assistant_bench.families import (
    Family,
    TaskTestsSettings,
    VotesSettings,
    load_cases,
    load_family,
)
from assistant_bench.paths import FAMILIES_DIR


def write_family(
    root: Path, name: str, settings: str, *, cases: bool, tasks: bool
) -> None:
    directory = root / name
    directory.mkdir(parents=True)
    (directory / 'family.toml').write_text(textwrap.dedent(settings))
    if cases:
        (directory / 'cases.jsonl').write_text('')
    if tasks:
        (directory / 'tasks').mkdir()


def case_line(
    case_id: str, source: str = 'synthetic', category: str = 'message'
) -> str:
    case = {'id': case_id, 'category': category, 'source': source, 'instruction': 'Hi.'}
    return json.dumps(case) + '\n'


def write_case_family(families_dir: Path) -> Path:
    """Write family `f` listing the `message` category, returning its cases file."""
    write_family(
        families_dir,
        'f',
        """\
        scorer = "votes"
        display_name = "Family F"
        systems = ["large"]
        samples = 1
        max_output_tokens = 300
        categories = ["message"]
        reference = "large"
        """,
        cases=True,
        tasks=False,
    )
    return families_dir / 'f' / 'cases.jsonl'


class TestLoadFamily:
    def test_reads_a_case_family_scored_by_votes(self, families_dir: Path) -> None:
        write_family(
            families_dir,
            'writing-f',
            """\
            scorer = "votes"
            aspect = "writing"
            display_name = "Family F"
            systems = ["large", "small"]
            samples = 2
            max_output_tokens = 300
            reference = "large"
            render = "html"
            public = true
            """,
            cases=True,
            tasks=False,
        )

        family = load_family('writing-f')

        assert family == Family(
            name='writing-f',
            source='cases',
            settings=VotesSettings(
                scorer='votes',
                aspect='writing',
                display_name='Family F',
                systems=['large', 'small'],
                samples=2,
                max_output_tokens=300,
                reference='large',
                render='html',
                public=True,
            ),
        )

    def test_reads_a_task_family_scored_by_tests(self) -> None:
        assert load_family('smoke') == Family(
            name='smoke',
            source='tasks',
            settings=TaskTestsSettings(
                scorer='tests', display_name='Smoke Test · Tasks', samples=1
            ),
        )

    def test_names_a_missing_family(self, families_dir: Path) -> None:
        with pytest.raises(ValueError, match="^no family named 'nope'$"):
            load_family('nope')

    @pytest.mark.parametrize(
        ('name', 'settings', 'cases', 'tasks', 'error', 'message'),
        [
            pytest.param(
                'f',
                """\
                scorer = "votes"
                display_name = "Family F"
                systems = ["large"]
                samples = 1
                max_output_tokens = 300
                reference = "large"
                public = true
                """,
                True,
                False,
                ValueError,
                "family 'f': a public family needs an aspect",
                id='public-without-an-aspect',
            ),
            pytest.param(
                'writingf',
                """\
                scorer = "votes"
                aspect = "writing"
                display_name = "Family F"
                systems = ["large"]
                samples = 1
                max_output_tokens = 300
                reference = "large"
                public = true
                """,
                True,
                False,
                ValueError,
                "family 'writingf': a public family ID starts with its aspect, "
                'as in writing-<name>',
                id='public-id-outside-its-aspect',
            ),
            pytest.param(
                'f',
                """\
                scorer = "tests"
                display_name = "Family F"
                samples = 1
                """,
                True,
                True,
                ValueError,
                'expected exactly one of cases.jsonl',
                id='cases-and-tasks',
            ),
            pytest.param(
                'f',
                """\
                scorer = "tests"
                display_name = "Family F"
                samples = 1
                """,
                False,
                False,
                ValueError,
                'expected exactly one of cases.jsonl',
                id='no-task-source',
            ),
            pytest.param(
                'f',
                """\
                scorer = "votes"
                display_name = "Family F"
                systems = ["large"]
                samples = 1
                reference = "large"
                """,
                True,
                False,
                ValueError,
                'case families need max_output_tokens',
                id='cases-without-an-output-bound',
            ),
            pytest.param(
                'f',
                """\
                scorer = "votes"
                display_name = "Family F"
                systems = ["large", "small"]
                samples = 1
                max_output_tokens = 300
                reference = "medium"
                """,
                True,
                False,
                ValidationError,
                "reference 'medium' is not in",
                id='reference-outside-the-systems',
            ),
            pytest.param(
                'f',
                """\
                scorer = "votes"
                display_name = "Family F"
                systems = ["large"]
                samples = 1
                max_output_tokens = 300
                reference = "large"

                [gates]
                min_chars = 500
                max_chars = 400
                """,
                True,
                False,
                ValidationError,
                'min_chars 500 exceeds max_chars 400',
                id='inverted-length-bounds',
            ),
            pytest.param(
                'f',
                """\
                scorer = "judges"
                display_name = "Family F"
                samples = 1
                """,
                False,
                True,
                ValidationError,
                "tag 'judges' found using 'scorer'",
                id='unknown-scorer',
            ),
            pytest.param(
                'f',
                """\
                scorer = "tests"
                samples = 1
                """,
                False,
                True,
                ValidationError,
                'display_name',
                id='no-display-name',
            ),
        ],
    )
    def test_rejects_a_family_that_breaks_a_rule(
        self,
        families_dir: Path,
        name: str,
        settings: str,
        cases: bool,
        tasks: bool,
        error: type[Exception],
        message: str,
    ) -> None:
        write_family(families_dir, name, settings, cases=cases, tasks=tasks)

        with pytest.raises(error, match=message):
            load_family(name)

    def test_rejects_cases_scored_by_tests(self, families_dir: Path) -> None:
        write_family(
            families_dir,
            'f',
            """\
            scorer = "tests"
            display_name = "Family F"
            samples = 1
            max_output_tokens = 300
            """,
            cases=True,
            tasks=False,
        )

        with pytest.raises(ValueError) as error:
            load_family('f')

        assert str(error.value) == (
            'family \'f\': scorer "tests" needs tasks/ with their own tests, '
            'so score cases.jsonl by "votes"'
        )

    def test_accepts_equal_length_bounds(self, families_dir: Path) -> None:
        write_family(
            families_dir,
            'f',
            """\
            scorer = "votes"
            display_name = "Family F"
            systems = ["large"]
            samples = 1
            max_output_tokens = 300
            reference = "large"

            [gates]
            min_chars = 400
            max_chars = 400
            """,
            cases=True,
            tasks=False,
        )

        family = load_family('f')

        assert isinstance(family.settings, VotesSettings)
        assert (family.settings.gates.min_chars, family.settings.gates.max_chars) == (
            400,
            400,
        )


class TestLoadCases:
    def test_appends_local_cases_to_the_committed_ones(self, data_dir: Path) -> None:
        local = data_dir / 'cases' / 'smoke-text.jsonl'
        local.parent.mkdir(parents=True)
        local.write_text(case_line('zh-local', source='held-out'))

        cases = load_cases(load_family('smoke-text'))

        assert [case.id for case in cases] == ['en-greeting', 'zh-greeting', 'zh-local']
        assert cases[-1].source == 'held-out'

    def test_rejects_a_local_case_that_reuses_a_committed_id(
        self, data_dir: Path
    ) -> None:
        local = data_dir / 'cases' / 'smoke-text.jsonl'
        local.parent.mkdir(parents=True)
        local.write_text(case_line('zh-local') + case_line('en-greeting'))
        committed = FAMILIES_DIR / 'smoke-text' / 'cases.jsonl'

        with pytest.raises(ValueError) as error:
            load_cases(load_family('smoke-text'))

        assert str(error.value) == (
            f"family 'smoke-text': case 'en-greeting' appears at {committed}:1 "
            f'and {local}:2'
        )

    def test_rejects_a_repeated_id_within_one_file(self, families_dir: Path) -> None:
        path = write_case_family(families_dir)
        path.write_text(case_line('a') + '\n' + case_line('a'))

        with pytest.raises(ValueError) as error:
            load_cases(load_family('f'))

        assert str(error.value) == (
            f"family 'f': case 'a' appears at {path}:1 and {path}:3"
        )

    def test_names_the_line_of_an_invalid_case(self, families_dir: Path) -> None:
        path = write_case_family(families_dir)
        path.write_text(case_line('a') + case_line('B'))

        with pytest.raises(ValueError) as error:
            load_cases(load_family('f'))

        assert str(error.value).startswith(
            f"family 'f': {path}:2 is not a valid case: 1 validation error for Case\nid"
        )

    def test_rejects_a_category_the_family_does_not_list(
        self, families_dir: Path
    ) -> None:
        path = write_case_family(families_dir)
        path.write_text(case_line('a') + case_line('b', category='private-topic'))

        with pytest.raises(ValueError) as error:
            load_cases(load_family('f'))

        assert str(error.value) == (
            f"family 'f': case 'b' at {path}:2 has category 'private-topic', "
            'which categories does not list'
        )
