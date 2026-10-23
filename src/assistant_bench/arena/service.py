"""Voting sessions over blinded pairs, with each pair's systems kept server-side."""

import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Literal, assert_never

import numpy as np
from pydantic import BaseModel, Field

from assistant_bench.arena.resolution import reconcile
from assistant_bench.arena.scheduler import Board, Draw, Pool, draw
from assistant_bench.arena.store import (
    Ballot,
    Choice,
    Defects,
    Session,
    Side,
    Vote,
    VoteStore,
)
from assistant_bench.families import (
    Defect,
    TaskTestsSettings,
    VotesSettings,
    case_task,
    family_names,
    load_cases,
    load_family,
)
from assistant_bench.outputs import Output, read_outputs


OUTPUTS_PATH = '/outputs'


class UnknownItem(LookupError):
    """An item the arena does not hold.

    The item is a family the arena does not serve, or a session, pair, or
    document that it never issued or has retired.
    """


class NothingToUndo(Exception):
    """The session holds no vote of the owner's to undo."""


class UnlistedDefects(ValueError):
    """A vote tags an output with defects its family does not list."""


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
    """A pair as the vote page receives it, identifying no system.

    `token` is opaque and valid until the pair is voted on or undone.
    """

    token: str
    instruction: str
    checklist: list[str]
    a: Pane
    b: Pane


class FamilyView(BaseModel):
    """A family the vote page can start a session on, where `name` is its ID.

    The vote page composes the family's full name from `aspect` and `display_name`,
    and offers `defects` to tag each output with, in the family's order.
    """

    name: str
    aspect: str | None
    display_name: str
    defects: list[Defect]


class SessionView(BaseModel):
    """A session's votes and the pair to show, absent once no case remains.

    `remaining` counts the cases the session has yet to vote on, including the
    open pair's.
    """

    id: str
    family: FamilyView
    votes: int
    remaining: int
    pair: PairView | None


# ── Arena ──


@dataclass(frozen=True)
class ArenaFamily:
    """A family being voted on, with each voted case's instruction and checklist.

    `instructions` holds the rendered task's instruction, which the trials ran.
    """

    name: str
    aspect: str | None
    display_name: str
    render: Literal['text', 'html', 'diff']
    defects: list[Defect]
    instructions: dict[str, str]
    checklists: dict[str, list[str]]
    pool: Pool


@dataclass(frozen=True)
class _Issued:
    """A pair shown to a session, with the tokens of the documents its panes load."""

    session: str
    draw: Draw
    view: PairView
    documents: tuple[str, ...]
    served_at: datetime


def load_families() -> dict[str, ArenaFamily]:
    """Return every family scored by votes that has outputs."""
    families = {}
    for name in family_names():
        family = load_family(name)
        match family.settings:
            case VotesSettings():
                outputs = read_outputs(name)
                if outputs:
                    cases = {case.id: case for case in load_cases(family)}
                    pool = Pool.build(family.settings.systems, cases, outputs)
                    families[name] = ArenaFamily(
                        name=name,
                        aspect=family.settings.aspect,
                        display_name=family.settings.display_name,
                        render=family.settings.render,
                        defects=family.settings.defects,
                        instructions={
                            case: (
                                case_task(family, case) / 'instruction.md'
                            ).read_text()
                            for case in pool.outputs_by_case
                        },
                        checklists={
                            case: cases[case].checklist for case in pool.outputs_by_case
                        },
                        pool=pool,
                    )
            case TaskTestsSettings():
                pass
            case _:
                assert_never(family.settings)
    return families


class Arena:
    """Sessions, pairs, and votes for the families being voted on.

    Pairs that the outputs decide, by a gate failure or identical text, never
    reach the owner, and their results enter the fit from `Pool.settled`.
    """

    def __init__(
        self,
        store: VoteStore,
        families: dict[str, ArenaFamily],
        render_origin: str,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        rng: np.random.Generator | None = None,
    ) -> None:
        self._store = store
        self._families = families
        self._render_origin = render_origin
        self._clock = clock
        self._rng = rng or np.random.default_rng()
        self._boards = {
            name: Board(family.pool.systems) for name, family in families.items()
        }
        self._pairs: dict[str, _Issued] = {}
        self._current: dict[str, str] = {}
        self._documents: dict[str, str] = {}

    def families(self) -> list[FamilyView]:
        return [self._family_view(name) for name in sorted(self._families)]

    def start(self, family: str) -> SessionView:
        if family not in self._families:
            raise UnknownItem(family)
        self._boards[family].reset()
        return self._view(self._store.start_session(family, self._clock()))

    def resume(self, session_id: str) -> SessionView:
        return self._view(self._session(session_id))

    def vote(
        self, token: str, choice: Choice, defects: Defects, note: str | None
    ) -> SessionView:
        """Record the owner's vote on a pair and retire the pair.

        Raises
        ------
        UnknownItem
            The arena never issued the pair or has retired it.
        UnlistedDefects
            A side's defects include one the family does not list, which
            leaves the pair open.
        """
        if token not in self._pairs:
            raise UnknownItem(token)
        session = self._session(self._pairs[token].session)
        listed = self._families[session.family].defects
        unlisted = sorted((defects.a | defects.b) - set(listed))
        if unlisted:
            raise UnlistedDefects(
                f'issues the family does not list: {", ".join(unlisted)}'
            )
        issued = self._retire(token)
        self._store.record(
            Ballot(
                session=issued.session,
                case=issued.draw.case,
                a=Side.of(issued.draw.a),
                b=Side.of(issued.draw.b),
                choice=choice,
                defects=defects,
                note=note,
                duration_sec=(self._clock() - issued.served_at).total_seconds(),
                repeat_of=issued.draw.repeat_of,
            ),
            self._clock(),
        )
        return self._view(session)

    def undo(self, session_id: str) -> SessionView:
        """Withdraw the session's latest vote and show its pair again.

        A pair whose outputs have left the pool, or that the outputs now settle,
        cannot return, so the session draws a fresh one.
        """
        session = self._session(session_id)
        votes = self._votes(session)
        if not votes:
            raise NothingToUndo(session_id)
        last = votes[-1]
        matched = reconcile([last], self._families[session.family].pool.outputs)
        self._store.undo(last.id, self._clock())
        if session.id in self._current:
            self._retire(self._current[session.id])
        for vote, a, b in matched:
            self._serve(session, Draw(vote.case, a, b, repeat_of=vote.repeat_of))
        return self._view(session)

    def document(self, token: str) -> str:
        """Return the HTML output behind a pane's URL."""
        if token not in self._documents:
            raise UnknownItem(token)
        return self._documents[token]

    def _session(self, session_id: str) -> Session:
        session = self._store.session(session_id)
        if session is None or session.family not in self._families:
            raise UnknownItem(session_id)
        return session

    def _votes(self, session: Session) -> list[Vote]:
        return [
            vote
            for vote in self._store.votes(session.family)
            if vote.session == session.id
        ]

    def _view(self, session: Session) -> SessionView:
        if session.id not in self._current:
            self._next(session)
        votes = self._votes(session)
        used = {vote.case for vote in votes}
        open_cases = self._families[session.family].pool.votable_cases() - used
        token = self._current.get(session.id)
        return SessionView(
            id=session.id,
            family=self._family_view(session.family),
            votes=len(votes),
            remaining=len(open_cases),
            pair=None if token is None else self._pairs[token].view,
        )

    def _family_view(self, name: str) -> FamilyView:
        family = self._families[name]
        return FamilyView(
            name=name,
            aspect=family.aspect,
            display_name=family.display_name,
            defects=family.defects,
        )

    def _next(self, session: Session) -> None:
        votes = self._store.votes(session.family)
        used = {vote.case for vote in votes if vote.session == session.id}
        pool = self._families[session.family].pool
        matched = reconcile(votes, pool.outputs)
        board = self._boards[session.family]
        picked = draw(pool, matched, used, self._clock(), self._rng, board)
        if picked is not None:
            self._serve(session, picked)

    def _serve(self, session: Session, picked: Draw) -> None:
        family = self._families[session.family]
        token = secrets.token_urlsafe(16)
        (a, a_document), (b, b_document) = (
            self._pane(family, output) for output in (picked.a, picked.b)
        )
        self._pairs[token] = _Issued(
            session=session.id,
            draw=picked,
            view=PairView(
                token=token,
                instruction=family.instructions[picked.case],
                checklist=family.checklists[picked.case],
                a=a,
                b=b,
            ),
            documents=tuple(
                document for document in (a_document, b_document) if document
            ),
            served_at=self._clock(),
        )
        self._current[session.id] = token

    def _pane(self, family: ArenaFamily, output: Output) -> tuple[Pane, str | None]:
        """Return the pane that shows `output`, with the token of its HTML document."""
        assert output.text is not None
        match family.render:
            case 'text':
                return TextPane(render='text', text=output.text), None
            case 'html':
                document = secrets.token_urlsafe(16)
                self._documents[document] = output.text
                url = f'{self._render_origin}{OUTPUTS_PATH}/{document}'
                return HtmlPane(render='html', url=url), document
            case 'diff':
                return DiffPane(render='diff', text=output.text), None
            case _:
                assert_never(family.render)

    def _retire(self, token: str) -> _Issued:
        issued = self._pairs.pop(token)
        del self._current[issued.session]
        for document in issued.documents:
            del self._documents[document]
        return issued
