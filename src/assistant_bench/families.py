"""Family settings, cases, and the merge of committed and local cases."""

from collections.abc import Iterator
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from assistant_bench.messages import InputError, code, error_lines, validated_toml
from assistant_bench.paths import FAMILIES_DIR


# ── Names and Votes ──


# Python JSON Schema validators accept a final newline with $.
type FamilyName = Annotated[
    str,
    Field(
        pattern=r'^[a-z0-9][a-z0-9-]*$',
        max_length=256,
        json_schema_extra={'pattern': r'^[a-z0-9][a-z0-9-]*(?![\s\S])'},
    ),
]

type Choice = Literal['a', 'b', 'tie', 'both_bad']

type Category = Literal[
    'bug-fix',
    'docs',
    'essay',
    'feature',
    'fiction',
    'message',
    'refactor',
    'reply',
    'review',
    'smoke',
]

type Defect = Literal[
    'facts',
    'mechanics',
    'tone',
    'formulaic',
    'length',
    'scope',
    'conventions',
    'complexity',
    'testing',
    'clarity',
]


# ── Settings ──


def _unique[T: str](values: list[T]) -> list[T]:
    """Reject a list with a repeated value."""
    repeated = sorted({value for value in values if values.count(value) > 1})
    if repeated:
        raise ValueError(f'repeats {code(repeated)}')
    return values


type Unique[T] = Annotated[list[T], AfterValidator(_unique)]


class _Settings(BaseModel):
    """Settings every family shares."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    aspect: str | None = None
    """Site grouping and public family ID prefix, validated by the site."""

    display_name: str = Field(min_length=1)
    """Short name within the aspect, or the whole name without one."""

    systems: Unique[str] = []
    samples: int = Field(ge=1)

    harness: str | None = None
    """Agent for every listed task-family system. Cases always use `generate`."""

    budget_usd: float | None = Field(default=None, gt=0)
    """Spend in USD that the family's ledger and a run's projection may reach."""

    categories: Unique[Category] = []
    """Allowed case category IDs, in board order. The site labels them."""

    public: bool = False
    """Whether the public site lists the family."""


type Language = Literal['en', 'zh']


class Gate(StrEnum):
    """A check whose failure makes a voted output an automatic loss in its pairs."""

    EMPTY = 'empty'
    TRUNCATED = 'truncated'
    """The endpoint reported a finish reason other than `stop`, or none."""
    LENGTH = 'length'
    """The stripped output falls outside the family's `min_chars` and `max_chars`."""
    LANGUAGE = 'language'
    """The output's prose and code comments are mostly in another language."""
    SCRIPT = 'script'
    """The output holds Traditional-only characters that the instruction lacks."""
    HTML = 'html'
    """The output violates the complete document or preview isolation contract."""
    TESTS = 'tests'
    """The task's verifier did not pass."""


class Gates(BaseModel):
    """Eligibility bounds for voted outputs, in characters of the stripped text."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    min_chars: int | None = Field(default=None, ge=1)
    max_chars: int | None = Field(default=None, ge=1)
    script: Literal['hans'] | None = None
    """Rejects Traditional-only characters absent from the instruction."""

    @model_validator(mode='after')
    def _check_bounds(self) -> Self:
        if (
            self.min_chars is not None
            and self.max_chars is not None
            and self.min_chars > self.max_chars
        ):
            raise ValueError(
                f'`min_chars` {self.min_chars} exceeds `max_chars` {self.max_chars}'
            )
        return self


class VotesSettings(_Settings):
    """A family ranked by blind pairwise votes."""

    scorer: Literal['votes']
    public_voting: bool = False
    """Publish the public and synthetic cases for voting in production."""

    reference: str
    """Listed system anchoring at `ranking.ANCHOR` when ranked.

    The most-voted system anchors when the reference does not rank.
    """

    render: Literal['text', 'diff', 'html'] = 'text'
    """Output presentation. Only task families yield diffs."""

    language: Language | None = None
    gates: Gates = Gates()
    """Text eligibility checks. Task families set neither `language` nor `gates`."""

    defects: Unique[Defect] = []
    """Available output tags, in presentation order."""

    @model_validator(mode='after')
    def _check_reference(self) -> Self:
        if self.reference not in self.systems:
            raise ValueError(f'reference `{self.reference}` is not in `systems`')
        return self


class TaskTestsSettings(_Settings):
    """A family scored by the reward of each Harbor task's own tests."""

    scorer: Literal['tests']


class PromptPair(BaseModel):
    """A controlled comparison of the same generation with added instructions."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    bare: str
    prompted: str


class CheckersSettings(_Settings):
    """A single response per case, ranked by all applicable format checks."""

    scorer: Literal['checkers']
    samples: int = Field(default=1, ge=1, le=1)
    pairs: list[PromptPair] = Field(min_length=1)

    @model_validator(mode='after')
    def _check_pairs(self) -> Self:
        names = [name for pair in self.pairs for name in (pair.bare, pair.prompted)]
        if len(set(names)) != len(names) or set(names) != set(self.systems):
            raise ValueError('pairs must cover every listed system exactly once')
        return self


type Settings = Annotated[
    VotesSettings | TaskTestsSettings | CheckersSettings, Field(discriminator='scorer')
]

_SETTINGS = TypeAdapter[Settings](Settings)


# ── Families and Cases ──


type CaseSource = Literal['synthetic', 'public', 'transcript', 'held-out']

type CheckRule = Literal[
    'ellipsis',
    'han-punctuation',
    'curly-quotes',
    'paragraph-lines',
    'list-spacing',
    'tree-comments',
    'forbidden-marker',
]


class CheckContext(BaseModel):
    """Scoring-only case facts, excluded from rendered instructions and versions."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    language: Language
    kind: Literal['prose', 'directory-tree'] = 'prose'
    rules: Unique[CheckRule] = Field(min_length=1)
    targets: Unique[CheckRule] = []
    quoted_input: list[Annotated[str, Field(min_length=1)]] = []
    forbidden_marker: str | None = Field(default=None, min_length=1)

    @model_validator(mode='after')
    def _check_applicability(self) -> Self:
        if 'han-punctuation' in self.rules and self.language != 'zh':
            raise ValueError('han-punctuation requires Chinese prose')
        if 'curly-quotes' in self.rules and self.language != 'en':
            raise ValueError('curly-quotes requires English prose')
        if 'tree-comments' in self.rules and self.kind != 'directory-tree':
            raise ValueError('tree-comments requires a directory tree')
        if ('forbidden-marker' in self.rules) != (self.forbidden_marker is not None):
            raise ValueError('forbidden-marker requires exactly one planted marker')
        if not set(self.targets) <= set(self.rules):
            raise ValueError('targets must name applicable rules')
        return self


class Family(BaseModel):
    """A directory under `families/`, holding either `cases.toml` or `tasks/`."""

    model_config = ConfigDict(frozen=True)

    name: str
    source: Literal['cases', 'tasks']
    settings: Settings
    source_root: Path = Field(
        default_factory=lambda: FAMILIES_DIR, exclude=True, repr=False
    )


class Case(BaseModel):
    """One entry in a family's `cases.toml`."""

    model_config = ConfigDict(extra='forbid')

    id: str = Field(pattern=r'^[a-z0-9][a-z0-9-]*$')
    category: Category
    source: CaseSource
    instruction: str
    checklist: list[str] = []
    checks: CheckContext | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


class _CaseManifest(BaseModel):
    """The authored cases document, in evaluation order."""

    model_config = ConfigDict(extra='forbid')

    cases: list[Case]


class _TaskMetadata(BaseModel):
    """The `[metadata]` slice of a task's `task.toml` that a case reads."""

    model_config = ConfigDict(extra='ignore', frozen=True)

    category: Category
    source: CaseSource


class _TaskSection(BaseModel):
    """The `[task]` slice of a task's `task.toml` that a case reads."""

    model_config = ConfigDict(extra='ignore', frozen=True)

    name: str


class _TaskManifest(BaseModel):
    """The slice of a task's `task.toml` that a case reads.

    Harbor's other keys are ignored, since `tasks.check_tasks` validates them.
    """

    model_config = ConfigDict(extra='ignore', frozen=True)

    task: _TaskSection
    metadata: _TaskMetadata


_CASE_MANIFEST = TypeAdapter(_CaseManifest)
_TASK_MANIFEST = TypeAdapter(_TaskManifest)


# ── Loading ──


def family_names(*, root: Path | None = None) -> list[str]:
    """Return the name of every directory under `families/` holding a `family.toml`."""
    directory = FAMILIES_DIR if root is None else root
    return sorted(path.parent.name for path in directory.glob('*/family.toml'))


def load_family(name: str, *, root: Path | None = None) -> Family:
    """Read `family.toml` and infer where the family's tasks come from.

    Raises
    ------
    InputError
        No family has the name, the family holds both or neither of
        `cases.toml` and `tasks/`, `family.toml` does not parse or validate, a
        public family's name does not start with its aspect, or the settings do
        not fit the source.
    """
    source_root = FAMILIES_DIR if root is None else root
    directory = source_root / name
    if not (directory / 'family.toml').is_file():
        raise InputError(
            f'No family named `{name}`. '
            f'Choose among {code(family_names(root=source_root))}.'
        )

    has_cases = (directory / 'cases.toml').is_file()
    if has_cases == (directory / 'tasks').is_dir():
        raise InputError(
            f'Family `{name}`: expected exactly one of `cases.toml` and `tasks/`.'
        )

    # The first part of each error's path is the scorer's tag.
    settings = validated_toml(
        _SETTINGS,
        (directory / 'family.toml').read_text(),
        f'Family `{name}`: `family.toml`',
        skip=1,
    )

    if settings.public:
        if settings.aspect is None:
            raise InputError(f'Family `{name}`: a public family needs an `aspect`.')
        if not name.startswith(f'{settings.aspect}-'):
            raise InputError(
                f'Family `{name}`: a public family ID starts with its aspect, '
                f'as in `{settings.aspect}-<name>`.'
            )

    family = Family(
        name=name,
        source='cases' if has_cases else 'tasks',
        settings=settings,
        source_root=source_root,
    )

    _check_source_settings(family)
    return family


def _check_source_settings(family: Family) -> None:
    """Raise unless the family's settings fit where its tasks come from."""
    settings = family.settings
    prefix = f'Family `{family.name}`'
    if family.source == 'cases':
        if not isinstance(settings, (VotesSettings, CheckersSettings)):
            raise InputError(
                f'{prefix}: score a `cases.toml` family by `votes`, since only '
                'a `tasks/` family has tests.'
            )
        if settings.harness is not None:
            raise InputError(
                f'{prefix}: a `cases.toml` family runs every system as `generate`, '
                'so remove `harness`.'
            )

        if isinstance(settings, VotesSettings) and settings.render == 'diff':
            raise InputError(
                f'{prefix}: a `cases.toml` family yields replies, so set `render` '
                'to `"text"` or `"html"`.'
            )
        return

    if isinstance(settings, CheckersSettings):
        raise InputError(f'{prefix}: `checkers` requires a `cases.toml` family.')

    if isinstance(settings, VotesSettings):
        gate_keys = [
            key for key in ('language', 'gates') if key in settings.model_fields_set
        ]
        if gate_keys:
            raise InputError(
                f"{prefix}: a `tasks/` family's tests are its only gate, so remove "
                f'{code(gate_keys)}.'
            )
        if settings.render != 'diff':
            raise InputError(
                f'{prefix}: a `tasks/` family scored by `votes` yields diffs, so set '
                '`render = "diff"`.'
            )

    if settings.systems and settings.harness is None:
        raise InputError(
            f'{prefix}: a `tasks/` family that lists systems needs `harness`.'
        )


def load_cases(family: Family, *, private_root: Path | None = None) -> list[Case]:
    """Return committed cases followed by explicitly selected private sources.

    A task family's cases are its task directories, each taking its ID from the
    directory name and its category, source, and instruction from the task.

    Raises
    ------
    InputError
        A case document does not parse or validate, a task has no `task.toml` or
        `instruction.md` or names itself apart from its directory, two cases share
        an ID, within one source or across both, or a case's category is missing
        from the family's `categories`.
    """
    entries = (
        _task_entries(family, private_root)
        if family.source == 'tasks'
        else _case_entries(family, private_root)
    )
    cases: dict[str, Case] = {}
    locations: dict[str, str] = {}
    for location, case in entries:
        if isinstance(family.settings, CheckersSettings) != (case.checks is not None):
            raise InputError(
                f'Family `{family.name}`: case `{case.id}` needs scoring context '
                'exactly when its scorer is `checkers`.'
            )
        if case.checks is not None and any(
            value not in case.instruction
            for value in [*case.checks.quoted_input, case.checks.forbidden_marker]
            if value is not None
        ):
            raise InputError(
                f'Family `{family.name}`: case `{case.id}` declares a quotation or '
                'planted marker absent from its instruction.'
            )
        if case.id in cases:
            raise InputError(
                f'Family `{family.name}`: case `{case.id}` appears at '
                f'`{locations[case.id]}` and `{location}`.'
            )
        if case.category not in family.settings.categories:
            raise InputError(
                f'Family `{family.name}`: case `{case.id}` at `{location}` has '
                f'category `{case.category}`, missing from `categories` in '
                '`family.toml`.'
            )
        cases[case.id] = case
        locations[case.id] = location

    return list(cases.values())


def _hidden(path: Path) -> bool:
    return path.name.startswith('.')


def check_case_entries(
    *, private_root: Path | None = None, families_root: Path | None = None
) -> None:
    """Refuse a visible private source entry that no family reads.

    A case family reads `cases/<family>.toml` and a task family each directory
    under `cases/<family>/`. Hidden entries are ignored, so backups can remain.

    Raises
    ------
    InputError
        A visible entry is one no family reads.
    """
    unread = (
        _unread_case_entries(private_root, families_root)
        if private_root is not None
        else []
    )
    if unread:
        raise InputError(
            f'No family reads {code(unread)} under the private source '
            '`cases/`. A `cases.toml` family reads `cases/<family>.toml` and a '
            '`tasks/` family reads `cases/<family>/<task>/`, so move each entry '
            'to one of those paths, out of `cases/`, or under a hidden directory '
            'such as `cases/.<name>/`.'
        )


def _unread_case_entries(root: Path, families_root: Path | None) -> list[Path]:
    """Return each unread entry, listing a task family's loose files one by one."""
    if not root.is_dir():
        return []

    directory = FAMILIES_DIR if families_root is None else families_root
    names = family_names(root=directory)
    case_families = {
        name for name in names if (directory / name / 'cases.toml').is_file()
    }
    task_families = set(names) - case_families

    unread: list[Path] = []
    for entry in sorted(root.iterdir()):
        if _hidden(entry):
            continue
        if entry.is_file():
            if entry.suffix != '.toml' or entry.stem not in case_families:
                unread.append(entry)
        elif entry.name not in task_families:
            unread.append(entry)
        else:
            unread.extend(
                child
                for child in sorted(entry.iterdir())
                if child.is_file() and not _hidden(child)
            )
    return unread


def _case_entries(
    family: Family, private_root: Path | None
) -> Iterator[tuple[str, Case]]:
    paths = [family.source_root / family.name / 'cases.toml']
    if private_root is not None:
        paths.append(private_root / f'{family.name}.toml')

    for path in paths:
        if not path.exists():
            continue
        manifest = validated_toml(
            _CASE_MANIFEST, path.read_text(), f'Family `{family.name}`: `{path}`'
        )
        for index, case in enumerate(manifest.cases):
            yield f'{path}:cases.{index}', case


def _task_entries(
    family: Family, private_root: Path | None
) -> Iterator[tuple[str, Case]]:
    for root, prefix in _task_roots(family, private_root):
        if not root.is_dir():
            continue
        for directory in sorted(
            path for path in root.iterdir() if path.is_dir() and not _hidden(path)
        ):
            location = f'{prefix}/{directory.name}'
            yield location, _task_case(family, directory, location)


def _task_case(family: Family, directory: Path, location: str) -> Case:
    config_path = directory / 'task.toml'
    if not config_path.is_file():
        raise InputError(
            f'Family `{family.name}`: task `{directory.name}` at `{location}` '
            'has no `task.toml`.'
        )

    manifest = validated_toml(
        _TASK_MANIFEST,
        config_path.read_text(),
        f'Family `{family.name}`: `{location}/task.toml`',
    )
    expected = f'{family.name}/{directory.name}'
    if manifest.task.name != expected:
        raise InputError(
            f'Family `{family.name}`: set `[task] name` to `"{expected}"` in '
            f'`{location}/task.toml`.'
        )

    instruction_path = directory / 'instruction.md'
    if not instruction_path.is_file():
        raise InputError(
            f'Family `{family.name}`: task `{directory.name}` at `{location}` '
            'has no `instruction.md`.'
        )

    try:
        return Case(
            id=directory.name,
            category=manifest.metadata.category,
            source=manifest.metadata.source,
            instruction=instruction_path.read_text(),
        )
    except ValidationError as error:
        raise InputError(
            error_lines(error, f'Family `{family.name}`: `{location}`')
        ) from error


# ── Task Paths ──


def _task_roots(family: Family, private_root: Path | None) -> list[tuple[Path, str]]:
    """Return each root of a task family's tasks and the label it reports with."""
    roots = [(family.source_root / family.name / 'tasks', 'tasks')]
    if private_root is not None:
        roots.append((private_root / family.name, f'cases/{family.name}'))
    return roots


def task_location(
    family: Family, task: Path, *, private_root: Path | None = None
) -> str:
    """Return the label that a task's own directory reports with."""
    return next(
        f'{prefix}/{task.name}'
        for root, prefix in _task_roots(family, private_root)
        if task.parent == root
    )


def case_task(
    family: Family, case_id: str, *, private_root: Path | None = None
) -> Path:
    """Return a task's committed or explicitly selected private source directory."""
    committed = family.source_root / family.name / 'tasks'
    if (committed / case_id).is_dir() or private_root is None:
        return committed / case_id
    return private_root / family.name / case_id
