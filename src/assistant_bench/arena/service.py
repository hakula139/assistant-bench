"""Voting sessions over blinded pairs, with each pair's systems kept server-side."""

import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Literal, assert_never

import numpy as np
from pydantic import BaseModel, Field

from assistant_bench.arena.scheduler import Draw, Pool, draw
from assistant_bench.arena.store import (
    Ballot,
    Choice,
    Flag,
    Resolution,
    Session,
    Side,
    Vote,
    VoteStore,
)
from assistant_bench.families import (
    Case,
    TaskTestsSettings,
    VotesSettings,
    family_names,
    load_cases,
    load_family,
)
from assistant_bench.outputs import Output, read_outputs


SESSION_VOTES = 30


class UnknownItem(LookupError):
    """A session, pair, or document that the arena never issued or has retired."""


class NothingToUndo(Exception):
    """The session holds no vote of the owner's to undo."""


# ── Views ──


class TextPane(BaseModel):
    render: Literal['text']
    text: str


class HtmlPane(BaseModel):
    """An HTML output, loaded in a sandboxed frame from the render origin."""

    render: Literal['html']
    url: str


type Pane = Annotated[TextPane | HtmlPane, Field(discriminator='render')]


class PairView(BaseModel):
    """A pair as the vote page receives it, identifying no system.

    `token` is opaque and valid until the pair is voted on or undone.
    """

    token: str
    instruction: str
    checklist: list[str]
    a: Pane
    b: Pane


class SessionView(BaseModel):
    """A session's progress and the pair to show, absent once the session ends.

    `size` is the number of votes the session can still reach.
    """

    id: str
    family: str
    votes: int
    size: int
    pair: PairView | None


# ── Arena ──


@dataclass(frozen=True)
class ArenaFamily:
    name: str
    render: Literal['text', 'html']
    cases: dict[str, Case]
    pool: Pool


@dataclass(frozen=True)
class _Issued:
    session: str
    draw: Draw
    view: PairView
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
                    families[name] = ArenaFamily(
                        name=name,
                        render=family.settings.render,
                        cases={case.id: case for case in load_cases(family)},
                        pool=Pool.build(family.settings.systems, outputs),
                    )
            case TaskTestsSettings():
                pass
            case _:
                assert_never(family.settings)
    return families


class Arena:
    """Sessions, pairs, and votes for the families being voted on.

    A pair resolves without reaching the owner when an output fails its gates,
    as a loss for the failing side or as both bad, or when both outputs are
    identical, as a tie.
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
        self._pairs: dict[str, _Issued] = {}
        self._current: dict[str, str] = {}
        self._documents: dict[str, str] = {}

    def families(self) -> list[str]:
        return sorted(self._families)

    def start(self, family: str) -> SessionView:
        if family not in self._families:
            raise UnknownItem(family)
        return self._view(self._store.start_session(family, self._clock()))

    def resume(self, session_id: str) -> SessionView:
        return self._view(self._session(session_id))

    def vote(
        self, token: str, choice: Choice, flags: frozenset[Flag], note: str | None
    ) -> SessionView:
        issued = self._retire(token)
        self._store.record(
            Ballot(
                session=issued.session,
                case=issued.draw.case,
                a=Side.of(issued.draw.a),
                b=Side.of(issued.draw.b),
                choice=choice,
                flags=flags,
                note=note,
                duration_sec=(self._clock() - issued.served_at).total_seconds(),
                repeat_of=issued.draw.repeat_of,
            ),
            self._clock(),
        )
        return self._view(self._session(issued.session))

    def undo(self, session_id: str) -> SessionView:
        """Withdraw the session's latest vote and show its pair again."""
        session = self._session(session_id)
        owned = [vote for vote in self._votes(session) if vote.auto is None]
        if not owned:
            raise NothingToUndo(session_id)
        last = owned[-1]
        self._store.undo(last.id, self._clock())
        if session.id in self._current:
            self._retire(self._current[session.id])

        pool = self._families[session.family].pool
        a, b = pool.find(last.case, last.a), pool.find(last.case, last.b)
        assert a is not None and b is not None
        self._serve(session, Draw(case=last.case, a=a, b=b, repeat_of=last.repeat_of))
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
        owned = sum(vote.auto is None for vote in votes)
        used = {vote.case for vote in votes}
        open_cases = self._families[session.family].pool.paired_cases() - used
        token = self._current.get(session.id)
        return SessionView(
            id=session.id,
            family=session.family,
            votes=owned,
            size=min(SESSION_VOTES, owned + len(open_cases)),
            pair=None if token is None else self._pairs[token].view,
        )

    def _next(self, session: Session) -> None:
        """Serve the next pair, recording the pairs that resolve on the way."""
        pool = self._families[session.family].pool
        while True:
            votes = self._store.votes(session.family)
            mine = [vote for vote in votes if vote.session == session.id]
            if sum(vote.auto is None for vote in mine) >= SESSION_VOTES:
                return
            picked = draw(
                pool, votes, {vote.case for vote in mine}, self._clock(), self._rng
            )
            if picked is None:
                return
            resolution = _resolve(picked)
            if resolution is None:
                self._serve(session, picked)
                return
            choice, auto = resolution
            self._store.record(
                Ballot(
                    session=session.id,
                    case=picked.case,
                    a=Side.of(picked.a),
                    b=Side.of(picked.b),
                    choice=choice,
                    auto=auto,
                    repeat_of=picked.repeat_of,
                ),
                self._clock(),
            )

    def _serve(self, session: Session, picked: Draw) -> None:
        family = self._families[session.family]
        case = family.cases[picked.case]
        token = secrets.token_urlsafe(16)
        self._pairs[token] = _Issued(
            session=session.id,
            draw=picked,
            view=PairView(
                token=token,
                instruction=case.instruction,
                checklist=case.checklist,
                a=self._pane(family, picked.a),
                b=self._pane(family, picked.b),
            ),
            served_at=self._clock(),
        )
        self._current[session.id] = token

    def _pane(self, family: ArenaFamily, output: Output) -> Pane:
        assert output.text is not None
        match family.render:
            case 'text':
                return TextPane(render='text', text=output.text)
            case 'html':
                document = secrets.token_urlsafe(16)
                self._documents[document] = output.text
                url = f'{self._render_origin}/outputs/{document}'
                return HtmlPane(render='html', url=url)
            case _:
                assert_never(family.render)

    def _retire(self, token: str) -> _Issued:
        if token not in self._pairs:
            raise UnknownItem(token)
        issued = self._pairs.pop(token)
        del self._current[issued.session]
        for pane in (issued.view.a, issued.view.b):
            if isinstance(pane, HtmlPane):
                del self._documents[pane.url.rsplit('/', 1)[1]]
        return issued


def _resolve(picked: Draw) -> tuple[Choice, Resolution] | None:
    match not picked.a.gate_failures, not picked.b.gate_failures:
        case False, False:
            return 'both_bad', 'gates'
        case False, True:
            return 'b', 'gates'
        case True, False:
            return 'a', 'gates'
    if picked.a.text == picked.b.text:
        return 'tie', 'identical'
    return None
