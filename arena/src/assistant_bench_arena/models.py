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


type FamilyName = Annotated[str, Field(pattern=r'^[a-z0-9][a-z0-9-]*$', max_length=256)]


# ── Choices ──


type Choice = Literal['a', 'b', 'tie', 'both_bad']

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


class Defects(BaseModel):
    """The defects tagged on each side's output."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    a: frozenset[Defect] = frozenset()
    b: frozenset[Defect] = frozenset()


# ── Views ──


class TextPane(BaseModel):
    render: Literal['text']
    text: str


class HtmlPane(BaseModel):
    """An HTML output, loaded in a sandboxed frame from the render origin."""

    render: Literal['html']
    url: str


class DiffPane(BaseModel):
    """A solution as a unified diff of the task's repository."""

    render: Literal['diff']
    text: str


type Pane = Annotated[TextPane | HtmlPane | DiffPane, Field(discriminator='render')]


class PairView(BaseModel):
    """A pair as the vote page receives it, identifying no system."""

    token: str
    """Opaque and valid until the pair is voted on or undone."""

    instruction: str
    checklist: list[str]
    a: Pane
    b: Pane


class FamilyView(BaseModel):
    """A family the vote page can start a session on."""

    name: FamilyName
    """Stable family ID."""

    aspect: str | None
    """Private aspects may use IDs outside the public site's registry."""

    display_name: str

    defects: list[Defect]
    """Available output tags, in the family's order."""


class SessionView(BaseModel):
    """A session's votes and the pair to show."""

    id: str
    family: FamilyView
    votes: int
    remaining: int
    """Cases still eligible for this session, including the open pair's."""

    pair: PairView | None
    """Absent once no eligible case remains."""


# ── Requests ──


NOTE_MAX_CHARS = 500


class StartSession(BaseModel):
    model_config = ConfigDict(extra='forbid')

    family: FamilyName


class VoteRequest(BaseModel):
    """A choice on a pair, where `a` is the left pane."""

    model_config = ConfigDict(extra='forbid')

    choice: Choice
    defects: Defects = Defects()
    note: str | None = Field(default=None, max_length=NOTE_MAX_CHARS)
