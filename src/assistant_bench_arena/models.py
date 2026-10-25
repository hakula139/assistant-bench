"""Voting requests and blinded browser views, independent of offline benchmark data."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


# ── Records ──


class Record(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
        frozen=True,
        strict=True,
        allow_inf_nan=False,
        use_attribute_docstrings=True,
    )


# ── Identifiers ──


# Python JSON Schema validators accept a final newline with $.
type FamilyName = Annotated[
    str,
    Field(
        pattern=r'^[a-z0-9][a-z0-9-]*$',
        max_length=256,
        json_schema_extra={'pattern': r'^[a-z0-9][a-z0-9-]*(?![\s\S])'},
    ),
]


# ── Choices ──


type Choice = Literal['a', 'b', 'tie', 'both_bad']

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


class Defects(BaseModel):
    """The defects tagged on each side's output."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    a: frozenset[Defect] = frozenset()
    b: frozenset[Defect] = frozenset()


# ── Views ──


class TextPane(Record):
    render: Literal['text']
    text: str


class HtmlPane(Record):
    """An HTML output, loaded in a sandboxed frame from the render origin."""

    render: Literal['html']
    url: str


class DiffPane(Record):
    """A solution as a unified diff of the task's repository."""

    render: Literal['diff']
    text: str


type Pane = Annotated[TextPane | HtmlPane | DiffPane, Field(discriminator='render')]


class FamilyView(Record):
    """A family the vote page can start a session on."""

    name: FamilyName
    """Stable family ID."""

    aspect: str | None
    """Site aspect ID, absent for family-specific boards."""

    display_name: str

    defects: list[Defect]
    """Available output tags, in the family's order."""
