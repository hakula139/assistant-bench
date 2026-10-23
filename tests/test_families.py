import json
import textwrap
from pathlib import Path
from typing import Any

import pytest
import tomli_w
from pydantic import ValidationError

from assistant_bench.families import (
    Family,
    TaskTestsSettings,
    VotesSettings,
    case_task,
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


def write_task(
    directory: Path,
    *,
    name: str,
    category: str | None = 'message',
    source: str | None = 'synthetic',
    instruction: str | None = 'Hi.',
    task_name: bool = True,
    task_toml: bool = True,
) -> None:
    """Write a task directory whose fields the caller may leave out."""
    directory.mkdir(parents=True)
    metadata = {
        key: value
        for key, value in (('category', category), ('source', source))
        if value is not None
    }
    task = {'version': '1.0.0'}
    if task_name:
        task['name'] = name
    if task_toml:
        (directory / 'task.toml').write_text(
            tomli_w.dumps({'schema_version': '1.4', 'task': task, 'metadata': metadata})
        )
    if instruction is not None:
        (directory / 'instruction.md').write_text(instruction)


def write_task_family(families_dir: Path) -> None:
    """Write family `f` with an empty tasks directory and the `message` category."""
    write_family(
        families_dir,
        'f',
        """\
        scorer = "tests"
        display_name = "Family F"
        samples = 1
        categories = ["message"]
        """,
        cases=False,
        tasks=True,
    )


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
                scorer='tests',
                display_name='Smoke Test · Tasks',
                samples=1,
                categories=['smoke'],
                max_output_tokens=64,
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

    def test_reads_a_task_family_from_its_task_directories(
        self, families_dir: Path
    ) -> None:
        write_task_family(families_dir)
        write_task(
            families_dir / 'f' / 'tasks' / 'a',
            name='f/a',
            instruction='Do it.',
        )
        write_task(
            families_dir / 'f' / 'tasks' / 'b',
            name='f/b',
            source='public',
        )

        cases = load_cases(load_family('f'))

        assert [
            (case.id, case.category, case.source, case.checklist) for case in cases
        ] == [
            ('a', 'message', 'synthetic', []),
            ('b', 'message', 'public', []),
        ]
        assert cases[0].instruction == 'Do it.'

    def test_appends_a_local_task_to_the_committed_ones(
        self, families_dir: Path, data_dir: Path
    ) -> None:
        write_task_family(families_dir)
        write_task(families_dir / 'f' / 'tasks' / 'a', name='f/a')
        write_task(data_dir / 'cases' / 'f' / 'b', name='f/b', source='held-out')

        cases = load_cases(load_family('f'))

        assert [(case.id, case.source) for case in cases] == [
            ('a', 'synthetic'),
            ('b', 'held-out'),
        ]

    def test_rejects_a_local_task_that_reuses_a_committed_id(
        self, families_dir: Path, data_dir: Path
    ) -> None:
        write_task_family(families_dir)
        write_task(families_dir / 'f' / 'tasks' / 'a', name='f/a')
        write_task(data_dir / 'cases' / 'f' / 'a', name='f/a', source='held-out')

        with pytest.raises(ValueError) as error:
            load_cases(load_family('f'))

        assert str(error.value) == (
            "family 'f': case 'a' appears at tasks/a and cases/f/a"
        )

    @pytest.mark.parametrize(
        ('options', 'message'),
        [
            pytest.param(
                {'task_toml': False},
                "family 'f': task 'a' at tasks/a has no task.toml",
                id='no-task-toml',
            ),
            pytest.param(
                {'instruction': None},
                "family 'f': task 'a' at tasks/a has no instruction.md",
                id='no-instruction',
            ),
            pytest.param(
                {'name': 'f/other'},
                'family \'f\': set [task] name to "f/a" in tasks/a/task.toml',
                id='name-apart-from-its-directory',
            ),
            pytest.param(
                {'task_name': False},
                "family 'f': tasks/a/task.toml is not valid: 1 validation error "
                'for _TaskManifest\ntask.name',
                id='no-name',
            ),
            pytest.param(
                {'category': None},
                "family 'f': tasks/a/task.toml is not valid: 1 validation error "
                'for _TaskManifest\nmetadata.category',
                id='no-category',
            ),
            pytest.param(
                {'source': None},
                "family 'f': tasks/a/task.toml is not valid: 1 validation error "
                'for _TaskManifest\nmetadata.source',
                id='no-source',
            ),
            pytest.param(
                {'source': 'private'},
                "family 'f': tasks/a/task.toml is not valid: 1 validation error "
                'for _TaskManifest\nmetadata.source',
                id='unknown-source',
            ),
            pytest.param(
                {'category': 'other'},
                "family 'f': case 'a' at tasks/a has category 'other', "
                'which categories does not list',
                id='unlisted-category',
            ),
        ],
    )
    def test_rejects_a_task_that_breaks_a_rule(
        self, families_dir: Path, options: dict[str, Any], message: str
    ) -> None:
        write_task_family(families_dir)
        write_task(families_dir / 'f' / 'tasks' / 'a', **({'name': 'f/a'} | options))

        with pytest.raises(ValueError) as error:
            load_cases(load_family('f'))

        assert str(error.value).startswith(message)

    @pytest.mark.parametrize(
        ('text', 'message'),
        [
            pytest.param(
                '[task',
                "family 'f': tasks/a/task.toml is not valid TOML:",
                id='malformed-toml',
            ),
            pytest.param(
                """\
                [task]
                name = "f/a"
                version = "1.0.0"
                metadata = "category source"
                """,
                "family 'f': tasks/a/task.toml is not valid:",
                id='metadata-as-a-string',
            ),
            pytest.param(
                """\
                task = "f/a"

                [metadata]
                category = "message"
                source = "synthetic"
                """,
                "family 'f': tasks/a/task.toml is not valid:",
                id='task-as-a-string',
            ),
        ],
    )
    def test_rejects_a_task_toml_that_does_not_parse(
        self, families_dir: Path, text: str, message: str
    ) -> None:
        write_task_family(families_dir)
        task = families_dir / 'f' / 'tasks' / 'a'
        task.mkdir(parents=True)
        (task / 'task.toml').write_text(textwrap.dedent(text))
        (task / 'instruction.md').write_text('Hi.')

        with pytest.raises(ValueError) as error:
            load_cases(load_family('f'))

        assert str(error.value).startswith(message)

    def test_ignores_a_file_under_tasks(self, families_dir: Path) -> None:
        write_task_family(families_dir)
        write_task(families_dir / 'f' / 'tasks' / 'a', name='f/a')
        (families_dir / 'f' / 'tasks' / 'notes.md').write_text('Notes.')

        cases = load_cases(load_family('f'))

        assert [case.id for case in cases] == ['a']

    def test_rejects_a_task_id_that_is_not_a_slug(self, families_dir: Path) -> None:
        write_task_family(families_dir)
        write_task(families_dir / 'f' / 'tasks' / 'Bad', name='f/Bad')

        with pytest.raises(ValueError) as error:
            load_cases(load_family('f'))

        assert 'tasks/Bad/task.toml is not a valid case:' in str(error.value)
        assert 'id' in str(error.value)


class TestCaseTask:
    def test_finds_a_committed_task_and_a_local_one(
        self, families_dir: Path, data_dir: Path
    ) -> None:
        write_task_family(families_dir)
        committed = families_dir / 'f' / 'tasks' / 'a'
        local = data_dir / 'cases' / 'f' / 'b'
        write_task(committed, name='f/a')
        write_task(local, name='f/b', source='held-out')

        family = load_family('f')

        assert case_task(family, 'a') == committed
        assert case_task(family, 'b') == local
