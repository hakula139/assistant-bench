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
            'f',
            """\
            scorer = "votes"
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

        family = load_family('f')

        assert family == Family(
            name='f',
            source='cases',
            settings=VotesSettings(
                scorer='votes',
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

    @pytest.mark.parametrize(('cases', 'tasks'), [(True, True), (False, False)])
    def test_requires_exactly_one_task_source(
        self, families_dir: Path, cases: bool, tasks: bool
    ) -> None:
        write_family(
            families_dir,
            'f',
            """\
            scorer = "tests"
            display_name = "Family F"
            samples = 1
            """,
            cases=cases,
            tasks=tasks,
        )

        with pytest.raises(ValueError, match='expected exactly one of cases.jsonl'):
            load_family('f')

    def test_requires_an_output_bound_for_cases(self, families_dir: Path) -> None:
        write_family(
            families_dir,
            'f',
            """\
            scorer = "votes"
            display_name = "Family F"
            systems = ["large"]
            samples = 1
            reference = "large"
            """,
            cases=True,
            tasks=False,
        )

        with pytest.raises(ValueError, match='case families need max_output_tokens'):
            load_family('f')

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

    def test_requires_the_reference_among_the_systems(self, families_dir: Path) -> None:
        write_family(
            families_dir,
            'f',
            """\
            scorer = "votes"
            display_name = "Family F"
            systems = ["large", "small"]
            samples = 1
            max_output_tokens = 300
            reference = "medium"
            """,
            cases=True,
            tasks=False,
        )

        with pytest.raises(ValidationError, match="reference 'medium' is not in"):
            load_family('f')

    def test_rejects_inverted_length_bounds(self, families_dir: Path) -> None:
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
            min_chars = 500
            max_chars = 400
            """,
            cases=True,
            tasks=False,
        )

        with pytest.raises(
            ValidationError, match='min_chars 500 exceeds max_chars 400'
        ):
            load_family('f')

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

    def test_rejects_an_unknown_scorer(self, families_dir: Path) -> None:
        write_family(
            families_dir,
            'f',
            """\
            scorer = "judges"
            display_name = "Family F"
            samples = 1
            """,
            cases=False,
            tasks=True,
        )

        with pytest.raises(ValidationError):
            load_family('f')

    def test_requires_a_display_name(self, families_dir: Path) -> None:
        write_family(
            families_dir,
            'f',
            """\
            scorer = "tests"
            samples = 1
            """,
            cases=False,
            tasks=True,
        )

        with pytest.raises(ValidationError, match='display_name'):
            load_family('f')


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
