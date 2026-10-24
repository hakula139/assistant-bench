"""Family settings, cases, and the merge of committed and local cases."""

import tomllib
from collections.abc import Iterator
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from assistant_bench.messages import code, error_lines, sentence
from assistant_bench.paths import FAMILIES_DIR, data_dir


# ── Settings ──


class _Settings(BaseModel):
    """Settings every family shares.

    `aspect` names the skill the family measures, which the site groups families
    by and validates, and a public family's ID starts with it. `display_name` is
    the family's short name within its aspect, or its whole name without one.
    `categories` lists the categories its cases may carry, which reports publish,
    and `public` says whether the public site lists the family.

    `harness` names the agent that runs every listed system of a task family,
    and `max_turns` bounds that agent's turns, while a case family runs every
    system as `generate`.
    """

    model_config = ConfigDict(extra='forbid', frozen=True)

    aspect: str | None = None
    display_name: str = Field(min_length=1)
    systems: list[str] = []
    samples: int = Field(ge=1)
    harness: str | None = None
    max_turns: int | None = Field(default=None, ge=1)
    max_output_tokens: int | None = Field(default=None, ge=1)
    categories: list[str] = []
    public: bool = False

    @model_validator(mode='after')
    def _check_systems(self) -> Self:
        repeated = sorted({s for s in self.systems if self.systems.count(s) > 1})
        if repeated:
            raise ValueError(f'`systems` repeats {code(repeated)}')
        return self


type Language = Literal['en', 'zh']

type Defect = Literal[
    'facts',
    'punctuation',
    'tone',
    'formulaic',
    'length',
    'scope',
    'conventions',
    'complexity',
    'testing',
    'clarity',
]

MAX_DEFECTS = 5


class Gate(StrEnum):
    """A check whose failure makes a voted output an automatic loss in its pairs."""

    EMPTY = 'empty'
    """The output is blank."""
    TRUNCATED = 'truncated'
    """The endpoint reported a finish reason other than `stop`, or none."""
    LENGTH = 'length'
    """The stripped output falls outside the family's `min_chars` and `max_chars`."""
    LANGUAGE = 'language'
    """The output's prose and code comments are mostly in another language."""
    SCRIPT = 'script'
    """The output holds Traditional-only characters that the instruction lacks."""
    TESTS = 'tests'
    """The task's hidden tests failed."""


class Gates(BaseModel):
    """Eligibility bounds for voted outputs, in characters of the stripped text.

    `script = "hans"` rejects Traditional-only characters the instruction lacks.
    """

    model_config = ConfigDict(extra='forbid', frozen=True)

    min_chars: int | None = Field(default=None, ge=1)
    max_chars: int | None = Field(default=None, ge=1)
    script: Literal['hans'] | None = None

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
    """A family ranked by the owner's blind pairwise votes.

    `reference` names the listed system whose score anchors the board at
    `ranking.ANCHOR` when the round ranks it, and otherwise the most-voted
    system anchors in its place. `render` says whether the vote page shows
    outputs as text, as sandboxed HTML, or as diffs, which only a task family
    yields, and `language` is the language every output must be written in,
    which the gates check when it is set. A task family's only gate is its
    tests, so it sets neither `language` nor `gates`. `defects` lists the
    issues the owner can tag an output with, in the vote page's key order.
    """

    scorer: Literal['votes']
    reference: str
    render: Literal['text', 'html', 'diff'] = 'text'
    language: Language | None = None
    gates: Gates = Gates()
    defects: list[Defect] = Field(default=[], max_length=MAX_DEFECTS)

    @model_validator(mode='after')
    def _check_reference(self) -> Self:
        if self.reference not in self.systems:
            raise ValueError(f'reference `{self.reference}` is not in `systems`')
        return self

    @model_validator(mode='after')
    def _check_defects(self) -> Self:
        repeated = sorted({d for d in self.defects if self.defects.count(d) > 1})
        if repeated:
            raise ValueError(f'`defects` repeats {code(repeated)}')
        return self


class TaskTestsSettings(_Settings):
    """A family scored by the reward of each Harbor task's own tests."""

    scorer: Literal['tests']


type Settings = Annotated[
    VotesSettings | TaskTestsSettings, Field(discriminator='scorer')
]

_SETTINGS = TypeAdapter[Settings](Settings)


# ── Families and Cases ──


type CaseSource = Literal['synthetic', 'public', 'transcript', 'held-out']


class Family(BaseModel):
    """A directory under `families/`, holding either `cases.jsonl` or `tasks/`."""

    model_config = ConfigDict(frozen=True)

    name: str
    source: Literal['cases', 'tasks']
    settings: Settings


class Case(BaseModel):
    """One line of a family's `cases.jsonl`."""

    model_config = ConfigDict(extra='forbid')

    id: str = Field(pattern=r'^[a-z0-9][a-z0-9-]*$')
    category: str
    source: CaseSource
    instruction: str
    checklist: list[str] = []


class _TaskMetadata(BaseModel):
    """The `[metadata]` slice of a task's `task.toml` that a case reads."""

    model_config = ConfigDict(extra='ignore', frozen=True)

    category: str
    source: CaseSource


class _TaskSection(BaseModel):
    """The `[task]` slice of a task's `task.toml` that a case reads."""

    model_config = ConfigDict(extra='ignore', frozen=True)

    name: str


class _TaskManifest(BaseModel):
    """The slice of a task's `task.toml` that a case reads.

    Harbor's other keys are ignored, since `jobs.check_tasks` validates them.
    """

    model_config = ConfigDict(extra='ignore', frozen=True)

    task: _TaskSection
    metadata: _TaskMetadata


# ── Loading ──


def family_names() -> list[str]:
    """Return the name of every directory under `families/` holding a `family.toml`."""
    return sorted(path.parent.name for path in FAMILIES_DIR.glob('*/family.toml'))


def load_family(name: str) -> Family:
    """Read `family.toml` and infer where the family's tasks come from.

    Raises
    ------
    ValueError
        No family has the name, the family holds both or neither of
        `cases.jsonl` and `tasks/`, `family.toml` does not parse or validate, a
        public family sets no aspect or names itself without that aspect's
        prefix, or the settings do not fit the source. A case family is scored by
        `votes`, needs `max_output_tokens`, sets no `harness` or `max_turns`,
        and renders no diffs. A task family sets no `language` or `gates` and
        renders diffs when scored by votes, and one that lists systems needs
        `harness`, `max_turns`, and `max_output_tokens`.
    """
    directory = FAMILIES_DIR / name
    if not (directory / 'family.toml').is_file():
        raise ValueError(
            f'No family named `{name}`. Choose among {code(family_names())}.'
        )
    has_cases = (directory / 'cases.jsonl').is_file()
    if has_cases == (directory / 'tasks').is_dir():
        raise ValueError(
            f'Family `{name}`: expected exactly one of `cases.jsonl` and `tasks/`.'
        )

    prefix = f'Family `{name}`: `family.toml`'
    try:
        with (directory / 'family.toml').open('rb') as file:
            settings = _SETTINGS.validate_python(tomllib.load(file))
    except tomllib.TOMLDecodeError as error:
        raise ValueError(sentence(f'{prefix} is not valid TOML: {error}')) from error
    except ValidationError as error:
        # The first part of each error's path is the scorer's tag.
        raise ValueError(error_lines(error, prefix, skip=1)) from error
    if settings.public:
        if settings.aspect is None:
            raise ValueError(f'Family `{name}`: a public family needs an `aspect`.')
        if not name.startswith(f'{settings.aspect}-'):
            raise ValueError(
                f'Family `{name}`: a public family ID starts with its aspect, '
                f'as in `{settings.aspect}-<name>`.'
            )
    family = Family(
        name=name, source='cases' if has_cases else 'tasks', settings=settings
    )
    _check_source_settings(family)
    return family


def _check_source_settings(family: Family) -> None:
    """Raise unless the family's settings fit where its tasks come from."""
    settings = family.settings
    prefix = f'Family `{family.name}`'
    if family.source == 'cases':
        if not isinstance(settings, VotesSettings):
            raise ValueError(
                f'{prefix}: score a `cases.jsonl` family by `votes`, since only '
                'a `tasks/` family has tests.'
            )
        if settings.max_output_tokens is None:
            raise ValueError(
                f'{prefix}: a `cases.jsonl` family needs `max_output_tokens`.'
            )
        agent_keys = [
            key for key in ('harness', 'max_turns') if key in settings.model_fields_set
        ]
        if agent_keys:
            raise ValueError(
                f'{prefix}: a `cases.jsonl` family runs every system as `generate`, '
                f'so remove {code(agent_keys)}.'
            )
        if settings.render == 'diff':
            raise ValueError(
                f'{prefix}: a `cases.jsonl` family yields replies, so set `render` '
                'to `"text"` or `"html"`.'
            )
        return

    if isinstance(settings, VotesSettings):
        gate_keys = [
            key for key in ('language', 'gates') if key in settings.model_fields_set
        ]
        if gate_keys:
            raise ValueError(
                f"{prefix}: a `tasks/` family's tests are its only gate, so remove "
                f'{code(gate_keys)}.'
            )
        if settings.render != 'diff':
            raise ValueError(
                f'{prefix}: a `tasks/` family scored by `votes` yields diffs, so set '
                '`render = "diff"`.'
            )
    if settings.systems:
        missing = [
            key
            for key in ('harness', 'max_turns', 'max_output_tokens')
            if getattr(settings, key) is None
        ]
        if missing:
            raise ValueError(
                f'{prefix}: a `tasks/` family that lists systems needs {code(missing)}.'
            )


def load_cases(family: Family) -> list[Case]:
    """Return the committed cases followed by the local ones, if the family has any.

    A task family's cases are its task directories, each taking its ID from the
    directory name and its category, source, and instruction from the task.

    Raises
    ------
    ValueError
        A line is not a valid case, a task has no `task.toml` or `instruction.md`
        or names itself apart from its directory, two cases share an ID, within
        one source or across both, or a case's category is missing from the
        family's `categories`.
    """
    entries = _task_entries(family) if family.source == 'tasks' else _case_lines(family)
    cases: dict[str, Case] = {}
    locations: dict[str, str] = {}
    for location, case in entries:
        if case.id in cases:
            raise ValueError(
                f'Family `{family.name}`: case `{case.id}` appears at '
                f'`{locations[case.id]}` and `{location}`.'
            )
        if case.category not in family.settings.categories:
            raise ValueError(
                f'Family `{family.name}`: case `{case.id}` at `{location}` has '
                f'category `{case.category}`, missing from `categories` in '
                '`family.toml`.'
            )
        cases[case.id] = case
        locations[case.id] = location
    return list(cases.values())


def _case_lines(family: Family) -> Iterator[tuple[str, Case]]:
    paths = [
        FAMILIES_DIR / family.name / 'cases.jsonl',
        data_dir() / 'cases' / f'{family.name}.jsonl',
    ]
    for path in paths:
        if not path.exists():
            continue
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            if not line.strip():
                continue
            location = f'{path}:{number}'
            try:
                case = Case.model_validate_json(line)
            except ValidationError as error:
                raise ValueError(
                    error_lines(error, f'Family `{family.name}`: `{location}`')
                ) from error
            yield location, case


def _task_entries(family: Family) -> Iterator[tuple[str, Case]]:
    for root, prefix in _task_roots(family):
        if not root.is_dir():
            continue
        for directory in sorted(path for path in root.iterdir() if path.is_dir()):
            location = f'{prefix}/{directory.name}'
            yield location, _task_case(family, directory, location)


def _task_case(family: Family, directory: Path, location: str) -> Case:
    config_path = directory / 'task.toml'
    if not config_path.is_file():
        raise ValueError(
            f'Family `{family.name}`: task `{directory.name}` at `{location}` '
            'has no `task.toml`.'
        )
    prefix = f'Family `{family.name}`: `{location}/task.toml`'
    try:
        manifest = _TaskManifest.model_validate(tomllib.loads(config_path.read_text()))
    except tomllib.TOMLDecodeError as error:
        raise ValueError(sentence(f'{prefix} is not valid TOML: {error}')) from error
    except ValidationError as error:
        raise ValueError(error_lines(error, prefix)) from error
    expected = f'{family.name}/{directory.name}'
    if manifest.task.name != expected:
        raise ValueError(
            f'Family `{family.name}`: set `[task] name` to `"{expected}"` in '
            f'`{location}/task.toml`.'
        )
    instruction_path = directory / 'instruction.md'
    if not instruction_path.is_file():
        raise ValueError(
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
        raise ValueError(
            error_lines(error, f'Family `{family.name}`: `{location}`')
        ) from error


# ── Task Paths ──


def _task_roots(family: Family) -> list[tuple[Path, str]]:
    """Return each root of a task family's tasks and the label it reports with."""
    return [
        (FAMILIES_DIR / family.name / 'tasks', 'tasks'),
        (data_dir() / 'cases' / family.name, f'cases/{family.name}'),
    ]


def task_location(family: Family, task: Path) -> str:
    """Return the label that a task's own directory reports with."""
    return next(
        f'{prefix}/{task.name}'
        for root, prefix in _task_roots(family)
        if task.parent == root
    )


def tasks_dir(family: Family) -> Path:
    """Return the directory that `jobs.render_tasks` installs the family's tasks in."""
    return data_dir() / 'tasks' / family.name


def case_task(family: Family, case_id: str) -> Path:
    """Return a case's Harbor task directory.

    A case family's task is installed under the data directory, while a task
    family's task is its own directory, committed or local.
    """
    if family.source == 'cases':
        return tasks_dir(family) / case_id
    (committed, _), (local, _) = _task_roots(family)
    if (committed / case_id).is_dir():
        return committed / case_id
    return local / case_id
