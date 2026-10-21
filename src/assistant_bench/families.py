"""Family settings, cases, and the merge of committed and local cases."""

import tomllib
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from assistant_bench.paths import FAMILIES_DIR, data_dir


class _Settings(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)

    systems: list[str] = []
    samples: int = Field(ge=1)
    max_output_tokens: int | None = Field(default=None, ge=1)


class VotesSettings(_Settings):
    """A family ranked by the owner's blind pairwise votes."""

    scorer: Literal['votes']


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
    """Reads `family.toml` and infers where the family's tasks come from.

    Raises:
        ValueError: The family holds both or neither of `cases.jsonl` and `tasks/`,
            or a case family sets no `max_output_tokens`.
    """
    directory = FAMILIES_DIR / name
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
    """Returns the committed cases followed by the local ones, if the family has any."""
    paths = [
        FAMILIES_DIR / family.name / 'cases.jsonl',
        data_dir() / 'cases' / f'{family.name}.jsonl',
    ]
    return [
        Case.model_validate_json(line)
        for path in paths
        if path.exists()
        for line in path.read_text().splitlines()
        if line.strip()
    ]
