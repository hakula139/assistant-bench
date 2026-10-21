"""Family settings, cases, and the merge of committed and local cases."""

import tomllib
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from assistant_bench.paths import FAMILIES_DIR, data_dir


class _Settings(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)

    systems: list[str] = []
    samples: int = Field(ge=1)
    max_output_tokens: int | None = Field(default=None, ge=1)


type Language = Literal['en', 'zh']


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


class Gates(BaseModel):
    """Eligibility bounds for voted outputs, in characters of the stripped text.

    `script = "hans"` rejects Traditional-only characters absent from the
    instruction.
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
                f'min_chars {self.min_chars} exceeds max_chars {self.max_chars}'
            )
        return self


class VotesSettings(_Settings):
    """A family ranked by the owner's blind pairwise votes.

    `language` is the language every output must be written in, which the gates
    check when it is set.
    """

    scorer: Literal['votes']
    language: Language | None = None
    gates: Gates = Gates()


class TaskTestsSettings(_Settings):
    """A family scored by the reward of each Harbor task's own tests."""

    scorer: Literal['tests']


type Settings = Annotated[
    VotesSettings | TaskTestsSettings, Field(discriminator='scorer')
]

_SETTINGS = TypeAdapter[Settings](Settings)


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
    source: Literal['synthetic', 'public', 'transcript', 'held-out']
    instruction: str
    checklist: list[str] = []


def load_family(name: str) -> Family:
    """Read `family.toml` and infer where the family's tasks come from.

    Raises
    ------
    ValueError
        No family has the name, the family holds both or neither of
        `cases.jsonl` and `tasks/`, or a case family sets no `max_output_tokens`.
    """
    directory = FAMILIES_DIR / name
    if not (directory / 'family.toml').is_file():
        raise ValueError(f'no family named {name!r}')
    has_cases = (directory / 'cases.jsonl').is_file()
    if has_cases == (directory / 'tasks').is_dir():
        raise ValueError(
            f'family {name!r}: expected exactly one of cases.jsonl and tasks/'
        )

    with (directory / 'family.toml').open('rb') as file:
        settings = _SETTINGS.validate_python(tomllib.load(file))
    if has_cases and settings.max_output_tokens is None:
        raise ValueError(f'family {name!r}: case families need max_output_tokens')
    return Family(
        name=name, source='cases' if has_cases else 'tasks', settings=settings
    )


def load_cases(family: Family) -> list[Case]:
    """Return the committed cases followed by the local ones, if the family has any.

    Raises
    ------
    ValueError
        Two cases share an ID, within one file or across both.
    """
    paths = [
        FAMILIES_DIR / family.name / 'cases.jsonl',
        data_dir() / 'cases' / f'{family.name}.jsonl',
    ]
    cases: dict[str, Case] = {}
    sources: dict[str, str] = {}
    for path in paths:
        if not path.exists():
            continue
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            if not line.strip():
                continue
            case = Case.model_validate_json(line)
            source = f'{path}:{number}'
            if case.id in cases:
                raise ValueError(
                    f'family {family.name!r}: case {case.id!r} appears at '
                    f'{sources[case.id]} and {source}'
                )
            cases[case.id] = case
            sources[case.id] = source
    return list(cases.values())
