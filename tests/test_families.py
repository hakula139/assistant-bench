import textwrap
from pathlib import Path
from typing import Any, TypedDict

import pytest
import tomli_w
from pydantic import TypeAdapter, ValidationError

from assistant_bench import families
from assistant_bench.families import (
    Family,
    TaskTestsSettings,
    VotesSettings,
    case_task,
    load_cases,
    load_family,
)
from assistant_bench.paths import FAMILIES_DIR
from assistant_bench_arena import models
from support import CODE_DEFECTS, PROSE_DEFECTS, write_case_family, write_task_family


class TaskOptions(TypedDict, total=False):
    name: str
    category: str | None
    source: str | None
    instruction: str | None
    task_name: bool
    task_toml: bool


def write_family(
    root: Path, name: str, settings: str, *, cases: bool, tasks: bool
) -> None:
    directory = root / name
    directory.mkdir(parents=True)
    (directory / 'family.toml').write_text(textwrap.dedent(settings))
    if cases:
        (directory / 'cases.toml').write_text('cases = []\n')
    if tasks:
        (directory / 'tasks').mkdir()


def case_entry(
    case_id: str,
    source: str = 'synthetic',
    category: str = 'message',
    instruction: str = 'Hi.',
    checklist: list[str] | None = None,
) -> str:
    case: dict[str, Any] = {
        'id': case_id,
        'category': category,
        'source': source,
        'instruction': instruction,
    }
    if checklist is not None:
        case['checklist'] = checklist
    return '[[cases]]\n' + tomli_w.dumps(case)


def write_task(
    directory: Path,
    *,
    name: str | None = None,
    category: str | None = 'smoke',
    source: str | None = 'synthetic',
    instruction: str | None = 'Hi.',
    task_name: bool = True,
    task_toml: bool = True,
) -> None:
    """Write a task directory whose fields the caller may leave out.

    The task name defaults to the test family `f` and the directory name.
    """
    directory.mkdir(parents=True)
    metadata = {
        key: value
        for key, value in (('category', category), ('source', source))
        if value is not None
    }
    task = {'version': '1.0.0'}
    if task_name:
        task['name'] = f'f/{directory.name}' if name is None else name
    if task_toml:
        (directory / 'task.toml').write_text(
            tomli_w.dumps({'schema_version': '1.4', 'task': task, 'metadata': metadata})
        )
    if instruction is not None:
        (directory / 'instruction.md').write_text(instruction)


def accepts(adapter: TypeAdapter[str], value: str) -> bool:
    try:
        adapter.validate_python(value)
    except ValidationError:
        return False
    return True


class TestVoteTypes:
    @pytest.mark.parametrize(
        ('offline', 'contract'),
        [
            (TypeAdapter(families.FamilyName), TypeAdapter(models.FamilyName)),
            (TypeAdapter(families.Choice), TypeAdapter(models.Choice)),
            (TypeAdapter(families.Defect), TypeAdapter(models.Defect)),
        ],
        ids=['family-name', 'choice', 'defect'],
    )
    def test_match_the_voting_contract(
        self, offline: TypeAdapter[str], contract: TypeAdapter[str]
    ) -> None:
        offline_schema = offline.json_schema()
        contract_schema = contract.json_schema()

        assert offline_schema == contract_schema

    @pytest.mark.parametrize(
        ('name', 'valid'),
        [
            ('a', True),
            ('9x', True),
            ('a-', True),
            ('a' * 256, True),
            ('-a', False),
            ('A', False),
            ('a\n', False),
            ('a' * 257, False),
        ],
        ids=[
            'letter',
            'leading-digit',
            'trailing-hyphen',
            'longest',
            'leading-hyphen',
            'uppercase',
            'final-newline',
            'too-long',
        ],
    )
    def test_family_names_validate_like_the_voting_contract(
        self, name: str, valid: bool
    ) -> None:
        adapters = {
            'offline': TypeAdapter(families.FamilyName),
            'contract': TypeAdapter(models.FamilyName),
        }

        accepted = {side: accepts(adapter, name) for side, adapter in adapters.items()}

        assert accepted == {'offline': valid, 'contract': valid}


class TestLoadFamily:
    def test_reads_a_case_family_scored_by_votes(self, families_dir: Path) -> None:
        write_family(
            families_dir,
            'writing-f',
            """\
            scorer = "votes"
            aspect = "writing"
            display_name = "Family F"
            systems = ["small", "large"]
            samples = 2
            reference = "large"
            defects = [
              "scope", "facts", "clarity", "tone", "mechanics", "formulaic", "length"
            ]
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
                systems=['small', 'large'],
                samples=2,
                reference='large',
                defects=list(PROSE_DEFECTS),
                render='html',
                public=True,
            ),
        )

    def test_reads_a_task_family_scored_by_tests(self) -> None:
        family = load_family('smoke')

        assert family == Family(
            name='smoke',
            source='tasks',
            settings=TaskTestsSettings(
                scorer='tests',
                display_name='Smoke Test · Tasks',
                samples=1,
                categories=['smoke'],
            ),
        )

    def test_reads_a_task_family_scored_by_votes(self, families_dir: Path) -> None:
        write_task_family(families_dir, systems=('large', 'small'))

        family = load_family('f')

        assert family == Family(
            name='f',
            source='tasks',
            settings=VotesSettings(
                scorer='votes',
                display_name='Family f',
                systems=['large', 'small'],
                reference='large',
                render='diff',
                defects=list(CODE_DEFECTS),
                samples=1,
                harness='terminus-2',
                categories=['smoke'],
            ),
        )

    def test_names_a_missing_family_and_the_choices(self, families_dir: Path) -> None:
        write_task_family(families_dir, name='f')
        write_task_family(families_dir, name='g')

        with pytest.raises(ValueError) as error:
            load_family('nope')

        assert str(error.value) == 'No family named `nope`. Choose among `f`, `g`.'

    @pytest.mark.parametrize(
        ('name', 'settings', 'cases', 'tasks', 'message'),
        [
            pytest.param(
                'f',
                """\
                scorer = "votes"
                display_name = "Family F"
                systems = ["large"]
                samples = 1
                reference = "large"
                public = true
                """,
                True,
                False,
                'Family `f`: a public family needs an `aspect`.',
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
                reference = "large"
                public = true
                """,
                True,
                False,
                'Family `writingf`: a public family ID starts with its aspect, '
                'as in `writing-<name>`.',
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
                'Family `f`: expected exactly one of `cases.toml` and `tasks/`.',
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
                'Family `f`: expected exactly one of `cases.toml` and `tasks/`.',
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
                max_output_tokens = 300
                """,
                True,
                False,
                'Family `f`: `family.toml`: `max_output_tokens`: Extra inputs '
                'are not permitted.',
                id='case-output-budget',
            ),
            pytest.param(
                'f',
                """\
                scorer = "tests"
                display_name = "Family F"
                samples = 1
                """,
                True,
                False,
                'Family `f`: score a `cases.toml` family by `votes`, since only a '
                '`tasks/` family has tests.',
                id='cases-scored-by-tests',
            ),
            pytest.param(
                'f',
                """\
                scorer = "votes"
                display_name = "Family F"
                systems = ["large"]
                samples = 1
                reference = "large"
                harness = "terminus-2"
                """,
                True,
                False,
                'Family `f`: a `cases.toml` family runs every system as `generate`, '
                'so remove `harness`.',
                id='cases-with-an-agent-harness',
            ),
            pytest.param(
                'f',
                """\
                scorer = "votes"
                display_name = "Family F"
                systems = ["large"]
                samples = 1
                reference = "large"
                harness = "terminus-2"
                render = "diff"
                language = "en"

                [gates]
                """,
                False,
                True,
                "Family `f`: a `tasks/` family's tests are its only gate, so remove "
                '`language`, `gates`.',
                id='tasks-with-case-gates',
            ),
            pytest.param(
                'f',
                """\
                scorer = "votes"
                display_name = "Family F"
                systems = ["large"]
                samples = 1
                reference = "large"
                harness = "terminus-2"
                """,
                False,
                True,
                'Family `f`: a `tasks/` family scored by `votes` yields diffs, so set '
                '`render = "diff"`.',
                id='tasks-without-diffs',
            ),
            pytest.param(
                'f',
                """\
                scorer = "votes"
                display_name = "Family F"
                systems = ["large"]
                samples = 1
                reference = "large"
                render = "diff"
                """,
                True,
                False,
                'Family `f`: a `cases.toml` family yields replies, so set `render` '
                'to `"text"` or `"html"`.',
                id='cases-with-diffs',
            ),
            pytest.param(
                'f',
                """\
                scorer = "votes"
                display_name = "Family F"
                systems = ["large", "small", "large"]
                samples = 1
                reference = "large"
                """,
                True,
                False,
                'Family `f`: `family.toml`: `systems`: repeats `large`.',
                id='repeated-voted-systems',
            ),
            pytest.param(
                'f',
                """\
                scorer = "tests"
                display_name = "Family F"
                systems = ["large", "small", "large"]
                samples = 1
                harness = "terminus-2"
                """,
                False,
                True,
                'Family `f`: `family.toml`: `systems`: repeats `large`.',
                id='repeated-tested-systems',
            ),
            pytest.param(
                'f',
                """\
                scorer = "votes"
                display_name = "Family F"
                systems = ["large"]
                samples = 1
                reference = "large"
                defects = ["tone", "scope", "tone"]
                """,
                True,
                False,
                'Family `f`: `family.toml`: `defects`: repeats `tone`.',
                id='repeated-defects',
            ),
            pytest.param(
                'f',
                """\
                scorer = "votes"
                display_name = "Family F"
                systems = ["large"]
                samples = 1
                reference = "large"
                defects = ["punctuation"]
                """,
                True,
                False,
                'Family `f`: `family.toml`: `defects.0`: Input should be',
                id='retired-defect',
            ),
            pytest.param(
                'f',
                """\
                scorer = "votes"
                display_name = "Family F"
                systems = ["large"]
                samples = 1
                reference = "large"
                categories = ["message", "reply", "message"]
                """,
                True,
                False,
                'Family `f`: `family.toml`: `categories`: repeats `message`.',
                id='repeated-categories',
            ),
            pytest.param(
                'f',
                """\
                scorer = "votes"
                display_name = "Family F"
                systems = ["large"]
                samples = 1
                reference = "large"
                categories = ["punctuation"]
                """,
                True,
                False,
                'Family `f`: `family.toml`: `categories.0`: Input should be',
                id='retired-category',
            ),
            pytest.param(
                'f',
                """\
                scorer = "votes"
                display_name = "Family F"
                systems = ["large"]
                samples = 1
                reference = "large"
                defects = ["typos"]
                """,
                True,
                False,
                'Family `f`: `family.toml`: `defects.0`: Input should be',
                id='unknown-defect',
            ),
            pytest.param(
                'f',
                """\
                scorer = "votes"
                display_name = "Family F"
                systems = ["large"]
                samples = 1
                reference = "large"
                render = "diff"
                harness = "terminus-2"
                max_turns = 10
                """,
                False,
                True,
                'Family `f`: `family.toml`: `max_turns`: Extra inputs are not '
                'permitted.',
                id='task-turn-budget',
            ),
            pytest.param(
                'f',
                """\
                scorer = "tests"
                display_name = "Family F"
                systems = ["large"]
                samples = 1
                """,
                False,
                True,
                'Family `f`: a `tasks/` family that lists systems needs `harness`.',
                id='tests-without-a-harness',
            ),
            pytest.param(
                'f',
                """\
                scorer = "votes"
                display_name = "Family F"
                systems = ["large", "small"]
                samples = 1
                reference = "medium"
                """,
                True,
                False,
                'Family `f`: `family.toml`: reference `medium` is not in `systems`.',
                id='reference-outside-the-systems',
            ),
            pytest.param(
                'f',
                """\
                scorer = "votes"
                display_name = "Family F"
                systems = ["large"]
                samples = 1
                reference = "large"

                [gates]
                min_chars = 500
                max_chars = 400
                """,
                True,
                False,
                'Family `f`: `family.toml`: `gates`: `min_chars` 500 exceeds '
                '`max_chars` 400.',
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
                "Family `f`: `family.toml`: Input tag 'judges' found using 'scorer' "
                "does not match any of the expected tags: 'votes', 'tests'.",
                id='unknown-scorer',
            ),
            pytest.param(
                'f',
                """\
                scorer = "tests"
                samples = 0
                """,
                False,
                True,
                'Family `f`: `family.toml`: `display_name`: Field required.\n'
                'Family `f`: `family.toml`: `samples`: Input should be greater than '
                'or equal to 1.',
                id='one-line-per-error',
            ),
            pytest.param(
                'f',
                'scorer = "tests',
                False,
                True,
                'Family `f`: `family.toml` is not valid TOML:',
                id='malformed-toml',
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
        message: str,
    ) -> None:
        write_family(families_dir, name, settings, cases=cases, tasks=tasks)

        with pytest.raises(ValueError) as error:
            load_family(name)

        assert str(error.value).startswith(message)

    def test_accepts_equal_length_bounds(self, families_dir: Path) -> None:
        write_family(
            families_dir,
            'f',
            """\
            scorer = "votes"
            display_name = "Family F"
            systems = ["large"]
            samples = 1
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
    def test_keeps_document_order_verbatim_text_and_the_checklist_default(
        self, families_dir: Path
    ) -> None:
        path = write_case_family(families_dir, ['large'])
        instruction = '\n  Keep these edges.  \n'
        checklist = ['  Keep this edge.  ']
        path.write_text(
            case_entry('z-last', instruction=instruction, checklist=checklist)
            + case_entry('a-first')
        )

        cases = load_cases(load_family('f'))

        assert [case.id for case in cases] == ['z-last', 'a-first']
        assert cases[0].instruction == instruction
        assert cases[0].checklist == checklist
        assert cases[1].checklist == []

    @pytest.mark.parametrize(
        ('text', 'field', 'message'),
        [
            pytest.param('', 'cases', 'Field required.', id='missing-container'),
            pytest.param(
                'cases = "message"',
                'cases',
                'Input should be a valid list.',
                id='non-list-container',
            ),
            pytest.param(
                'cases = []\ncase = []',
                'case',
                'Extra inputs are not permitted.',
                id='unknown-container-key',
            ),
            pytest.param(
                'cases = ["message"]',
                'cases.0',
                'Input should be a valid dictionary or instance of Case.',
                id='non-case-entry',
            ),
            pytest.param(
                case_entry('a') + 'instructions = "Hi."',
                'cases.0.instructions',
                'Extra inputs are not permitted.',
                id='unknown-case-key',
            ),
            pytest.param(
                case_entry('a') + 'checklist = "Keep the path."',
                'cases.0.checklist',
                'Input should be a valid list.',
                id='non-list-checklist',
            ),
            pytest.param(
                case_entry('a', category='punctuation'),
                'cases.0.category',
                "Input should be 'bug-fix', 'docs', 'essay', 'feature', 'fiction', "
                "'message', 'refactor', 'reply', 'review' or 'smoke'.",
                id='retired-category',
            ),
        ],
    )
    def test_rejects_a_case_document_that_breaks_its_contract(
        self, families_dir: Path, text: str, field: str, message: str
    ) -> None:
        path = write_case_family(families_dir, ['large'])
        path.write_text(text)

        with pytest.raises(ValueError) as error:
            load_cases(load_family('f'))

        assert str(error.value) == f'Family `f`: `{path}`: `{field}`: {message}'

    def test_names_the_file_and_location_of_malformed_toml(
        self, families_dir: Path
    ) -> None:
        path = write_case_family(families_dir, ['large'])
        path.write_text('[[cases]\n')

        with pytest.raises(ValueError) as error:
            load_cases(load_family('f'))

        assert str(error.value).startswith(f'Family `f`: `{path}` is not valid TOML:')
        assert 'line 1, column' in str(error.value)

    def test_appends_local_cases_to_the_committed_ones(self, data_dir: Path) -> None:
        local = data_dir / 'cases' / 'smoke-text.toml'
        local.parent.mkdir(parents=True)
        local.write_text(case_entry('zh-local', source='held-out'))

        cases = load_cases(load_family('smoke-text'))

        assert [case.id for case in cases] == ['en-greeting', 'zh-greeting', 'zh-local']
        assert cases[-1].source == 'held-out'

    def test_rejects_a_local_case_that_reuses_a_committed_id(
        self, data_dir: Path
    ) -> None:
        local = data_dir / 'cases' / 'smoke-text.toml'
        local.parent.mkdir(parents=True)
        local.write_text(case_entry('zh-local') + case_entry('en-greeting'))
        committed = FAMILIES_DIR / 'smoke-text' / 'cases.toml'

        with pytest.raises(ValueError) as error:
            load_cases(load_family('smoke-text'))

        assert str(error.value) == (
            'Family `smoke-text`: case `en-greeting` appears at '
            f'`{committed}:cases.0` '
            f'and `{local}:cases.1`.'
        )

    @pytest.mark.parametrize(
        ('written', 'reported'),
        [
            pytest.param(
                ('writing_zh.toml',), ('writing_zh.toml',), id='unknown-family'
            ),
            pytest.param(
                ('smoke-text.jsonl',), ('smoke-text.jsonl',), id='unconverted-cases'
            ),
            pytest.param(
                ('smoke/notes.md',), ('smoke/notes.md',), id='file-beside-tasks'
            ),
            pytest.param(
                ('old-family/task.toml',), ('old-family',), id='removed-family'
            ),
            pytest.param(
                ('a.toml', 'b.toml'), ('a.toml', 'b.toml'), id='several-entries'
            ),
        ],
    )
    def test_rejects_data_case_entries_that_no_family_reads(
        self,
        data_dir: Path,
        written: tuple[str, ...],
        reported: tuple[str, ...],
    ) -> None:
        root = data_dir / 'cases'
        for name in written:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('cases = []\n')

        with pytest.raises(ValueError) as error:
            load_cases(load_family('smoke-text'))

        entries = ', '.join(f'`{root / name}`' for name in reported)
        assert str(error.value) == (
            f"No family reads {entries} under the data directory's `cases/`. A "
            '`cases.toml` family reads `cases/<family>.toml` and a `tasks/` family '
            'reads `cases/<family>/<task>/`, so move each entry to one of those '
            'paths, out of `cases/`, or under a hidden directory such as '
            '`cases/.<name>/`.'
        )

    @pytest.mark.parametrize(
        'relative',
        [
            pytest.param('writing-zh.toml', id='another-case-family'),
            pytest.param('smoke/held/task.toml', id='another-task-family'),
            pytest.param('.converted-backup/writing_zh.toml', id='hidden-backup'),
            pytest.param('smoke/.notes', id='hidden-file-beside-tasks'),
        ],
    )
    def test_accepts_data_case_entries_that_a_family_reads(
        self, data_dir: Path, relative: str
    ) -> None:
        local = data_dir / 'cases' / relative
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_text('cases = []\n')

        cases = load_cases(load_family('smoke-text'))

        assert [case.id for case in cases] == ['en-greeting', 'zh-greeting']

    def test_ignores_a_hidden_directory_beside_local_tasks(
        self, data_dir: Path
    ) -> None:
        backup = data_dir / 'cases' / 'smoke' / '.backup'
        backup.mkdir(parents=True)
        (backup / 'notes.md').write_text('Notes.')

        cases = load_cases(load_family('smoke'))

        assert [case.id for case in cases] == ['echo']

    def test_rejects_a_repeated_id_within_one_file(self, families_dir: Path) -> None:
        path = write_case_family(families_dir, ['large'])
        path.write_text(case_entry('a') + '\n' + case_entry('a'))

        with pytest.raises(ValueError) as error:
            load_cases(load_family('f'))

        assert str(error.value) == (
            f'Family `f`: case `a` appears at `{path}:cases.0` and `{path}:cases.1`.'
        )

    def test_names_the_entry_and_field_of_an_invalid_case(
        self, families_dir: Path
    ) -> None:
        path = write_case_family(families_dir, ['large'])
        path.write_text(case_entry('a') + case_entry('B'))

        with pytest.raises(ValueError) as error:
            load_cases(load_family('f'))

        assert str(error.value) == (
            f'Family `f`: `{path}`: `cases.1.id`: String should match pattern '
            "'^[a-z0-9][a-z0-9-]*$'."
        )

    def test_rejects_a_category_the_family_does_not_list(
        self, families_dir: Path
    ) -> None:
        path = write_case_family(families_dir, ['large'])
        path.write_text(case_entry('a') + case_entry('b', category='docs'))

        with pytest.raises(ValueError) as error:
            load_cases(load_family('f'))

        assert str(error.value) == (
            f'Family `f`: case `b` at `{path}:cases.1` has category `docs`, '
            'missing from `categories` in `family.toml`.'
        )

    def test_reads_a_task_family_from_its_task_directories(
        self, families_dir: Path
    ) -> None:
        write_task_family(families_dir)
        write_task(
            families_dir / 'f' / 'tasks' / 'a',
            instruction='Do it.',
        )
        write_task(
            families_dir / 'f' / 'tasks' / 'b',
            source='public',
        )

        cases = load_cases(load_family('f'))

        assert [
            (case.id, case.category, case.source, case.checklist) for case in cases
        ] == [
            ('a', 'smoke', 'synthetic', []),
            ('b', 'smoke', 'public', []),
        ]
        assert cases[0].instruction == 'Do it.'

    def test_appends_a_local_task_to_the_committed_ones(
        self, families_dir: Path, data_dir: Path
    ) -> None:
        write_task_family(families_dir)
        write_task(families_dir / 'f' / 'tasks' / 'a')
        write_task(data_dir / 'cases' / 'f' / 'b', source='held-out')

        cases = load_cases(load_family('f'))

        assert [(case.id, case.source) for case in cases] == [
            ('a', 'synthetic'),
            ('b', 'held-out'),
        ]

    def test_rejects_a_local_task_that_reuses_a_committed_id(
        self, families_dir: Path, data_dir: Path
    ) -> None:
        write_task_family(families_dir)
        write_task(families_dir / 'f' / 'tasks' / 'a')
        write_task(data_dir / 'cases' / 'f' / 'a', source='held-out')

        with pytest.raises(ValueError) as error:
            load_cases(load_family('f'))

        assert str(error.value) == (
            'Family `f`: case `a` appears at `tasks/a` and `cases/f/a`.'
        )

    @pytest.mark.parametrize(
        ('directory', 'options', 'message'),
        [
            pytest.param(
                'a',
                {'task_toml': False},
                'Family `f`: task `a` at `tasks/a` has no `task.toml`.',
                id='no-task-toml',
            ),
            pytest.param(
                'a',
                {'instruction': None},
                'Family `f`: task `a` at `tasks/a` has no `instruction.md`.',
                id='no-instruction',
            ),
            pytest.param(
                'a',
                {'name': 'f/other'},
                'Family `f`: set `[task] name` to `"f/a"` in `tasks/a/task.toml`.',
                id='name-apart-from-its-directory',
            ),
            pytest.param(
                'a',
                {'task_name': False},
                'Family `f`: `tasks/a/task.toml`: `task.name`: Field required.',
                id='no-name',
            ),
            pytest.param(
                'a',
                {'category': None},
                'Family `f`: `tasks/a/task.toml`: `metadata.category`: Field required.',
                id='no-category',
            ),
            pytest.param(
                'a',
                {'source': None},
                'Family `f`: `tasks/a/task.toml`: `metadata.source`: Field required.',
                id='no-source',
            ),
            pytest.param(
                'a',
                {'source': 'private'},
                'Family `f`: `tasks/a/task.toml`: `metadata.source`: Input should be '
                "'synthetic', 'public', 'transcript' or 'held-out'.",
                id='unknown-source',
            ),
            pytest.param(
                'a',
                {'category': 'docs'},
                'Family `f`: case `a` at `tasks/a` has category `docs`, '
                'missing from `categories` in `family.toml`.',
                id='unlisted-category',
            ),
            pytest.param(
                'Bad',
                {'name': 'f/Bad'},
                'Family `f`: `tasks/Bad`: `id`: String should match pattern '
                "'^[a-z0-9][a-z0-9-]*$'.",
                id='id-outside-the-slug-pattern',
            ),
        ],
    )
    def test_rejects_a_task_that_breaks_a_rule(
        self,
        families_dir: Path,
        directory: str,
        options: TaskOptions,
        message: str,
    ) -> None:
        write_task_family(families_dir)
        write_task(families_dir / 'f' / 'tasks' / directory, **options)

        with pytest.raises(ValueError) as error:
            load_cases(load_family('f'))

        assert str(error.value) == message

    @pytest.mark.parametrize(
        ('text', 'message'),
        [
            pytest.param(
                '[task',
                'Family `f`: `tasks/a/task.toml` is not valid TOML:',
                id='malformed-toml',
            ),
            pytest.param(
                """\
                [task]
                name = "f/a"
                version = "1.0.0"
                metadata = "category source"
                """,
                'Family `f`: `tasks/a/task.toml`: `metadata`: Field required.',
                id='metadata-as-a-string',
            ),
            pytest.param(
                """\
                task = "f/a"

                [metadata]
                category = "smoke"
                source = "synthetic"
                """,
                'Family `f`: `tasks/a/task.toml`: `task`: Input should be a valid '
                'dictionary or instance of _TaskSection.',
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
        write_task(families_dir / 'f' / 'tasks' / 'a')
        (families_dir / 'f' / 'tasks' / 'notes.md').write_text('Notes.')

        cases = load_cases(load_family('f'))

        assert [case.id for case in cases] == ['a']


class TestCaseTask:
    def test_finds_a_committed_task_and_a_local_one(
        self, families_dir: Path, data_dir: Path
    ) -> None:
        write_task_family(families_dir)
        committed = families_dir / 'f' / 'tasks' / 'a'
        local = data_dir / 'cases' / 'f' / 'b'
        write_task(committed)
        write_task(local, source='held-out')
        family = load_family('f')

        found = (case_task(family, 'a'), case_task(family, 'b'))

        assert found == (committed, local)
