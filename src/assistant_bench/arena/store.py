"""The append-only SQLite store of voting sessions, votes, and undos."""

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from assistant_bench.outputs import Output
from assistant_bench.paths import data_dir
from assistant_bench.scoring.ranking import Comparison


type Choice = Literal['a', 'b', 'tie', 'both_bad']
type Flag = Literal['meaning', 'punctuation', 'register', 'script', 'length']
# `gates` resolves a pair in which an output fails the family's gates, and
# `identical` a pair of identical outputs, neither reaching the owner.
type Resolution = Literal['gates', 'identical']

# Ties and both-bad votes count as half a win for each side.
_LEFT_SCORE: dict[Choice, float] = {'a': 1.0, 'b': 0.0, 'tie': 0.5, 'both_bad': 0.5}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  family TEXT NOT NULL,
  started_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS votes (
  id INTEGER PRIMARY KEY,
  session TEXT NOT NULL REFERENCES sessions (id),
  case_id TEXT NOT NULL,
  a_system TEXT NOT NULL,
  a_fingerprint TEXT,
  a_job TEXT NOT NULL,
  a_trial TEXT NOT NULL,
  b_system TEXT NOT NULL,
  b_fingerprint TEXT,
  b_job TEXT NOT NULL,
  b_trial TEXT NOT NULL,
  choice TEXT NOT NULL CHECK (choice IN ('a', 'b', 'tie', 'both_bad')),
  auto TEXT CHECK (auto IN ('gates', 'identical')),
  flags TEXT NOT NULL,
  note TEXT,
  duration_sec REAL,
  repeat_of INTEGER REFERENCES votes (id),
  recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS undos (
  vote INTEGER PRIMARY KEY REFERENCES votes (id),
  undone_at TEXT NOT NULL
);
"""

_APPEND_ONLY = """
CREATE TRIGGER IF NOT EXISTS {table}_no_{action} BEFORE {action} ON {table}
BEGIN SELECT RAISE(ABORT, 'the vote store is append-only'); END;
"""


class Session(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    family: str
    started_at: datetime


class Side(BaseModel):
    """The output shown on one side of a pair."""

    model_config = ConfigDict(frozen=True)

    system: str
    fingerprint: str | None
    job: str
    trial: str

    @classmethod
    def of(cls, output: Output) -> Side:
        return cls(
            system=output.system,
            fingerprint=output.fingerprint,
            job=output.job,
            trial=output.trial,
        )


class Ballot(BaseModel):
    """A vote to record, with side A shown on the left.

    `duration_sec` runs from serving the pair to receiving the vote, and is
    unset for a resolved pair. `repeat_of` names the vote that a side-swapped
    repeat repeats.
    """

    model_config = ConfigDict(frozen=True)

    session: str
    case: str
    a: Side
    b: Side
    choice: Choice
    auto: Resolution | None = None
    flags: frozenset[Flag] = frozenset()
    note: str | None = None
    duration_sec: float | None = None
    repeat_of: int | None = None


class Vote(Ballot):
    id: int
    recorded_at: datetime

    def comparison(self) -> Comparison:
        return Comparison(
            self.a.system, self.b.system, _LEFT_SCORE[self.choice], self.case
        )


class VoteStore:
    """Sessions and votes in SQLite, where an undo is a row of its own."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # The API's handlers share one connection on the event loop's thread, which
        # need not be the thread that opened it.
        self._db = sqlite3.connect(path, autocommit=True, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_SCHEMA)
        for table in ('sessions', 'votes', 'undos'):
            for action in ('UPDATE', 'DELETE'):
                self._db.execute(_APPEND_ONLY.format(table=table, action=action))

    def close(self) -> None:
        self._db.close()

    def start_session(self, family: str, at: datetime) -> Session:
        session = Session(id=uuid.uuid4().hex, family=family, started_at=at)
        self._db.execute(
            'INSERT INTO sessions VALUES (?, ?, ?)',
            (session.id, family, at.isoformat()),
        )
        return session

    def session(self, session_id: str) -> Session | None:
        row = self._db.execute(
            'SELECT * FROM sessions WHERE id = ?', (session_id,)
        ).fetchone()
        return None if row is None else Session.model_validate(dict(row))

    def sessions(self, family: str) -> list[Session]:
        """Return the family's sessions in the order they started."""
        rows = self._db.execute(
            'SELECT * FROM sessions WHERE family = ? ORDER BY started_at, id',
            (family,),
        )
        return [Session.model_validate(dict(row)) for row in rows]

    def record(self, ballot: Ballot, at: datetime) -> Vote:
        cursor = self._db.execute(
            'INSERT INTO votes VALUES '
            '(NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (
                ballot.session,
                ballot.case,
                *(getattr(ballot.a, key) for key in Side.model_fields),
                *(getattr(ballot.b, key) for key in Side.model_fields),
                ballot.choice,
                ballot.auto,
                json.dumps(sorted(ballot.flags)),
                ballot.note,
                ballot.duration_sec,
                ballot.repeat_of,
                at.isoformat(),
            ),
        )
        assert cursor.lastrowid is not None
        return Vote(id=cursor.lastrowid, recorded_at=at, **dict(ballot))

    def undo(self, vote_id: int, at: datetime) -> None:
        self._db.execute('INSERT INTO undos VALUES (?, ?)', (vote_id, at.isoformat()))

    def votes(self, family: str) -> list[Vote]:
        """Return the family's votes that no undo withdrew, in recording order."""
        rows = self._db.execute(
            'SELECT votes.* FROM votes '
            'JOIN sessions ON sessions.id = votes.session '
            'LEFT JOIN undos ON undos.vote = votes.id '
            'WHERE sessions.family = ? AND undos.vote IS NULL '
            'ORDER BY votes.id',
            (family,),
        )
        return [_vote(row) for row in rows]


def votes_file() -> Path:
    return data_dir() / 'votes.sqlite'


def _vote(row: sqlite3.Row) -> Vote:
    sides = {
        side: Side(
            system=row[f'{side}_system'],
            fingerprint=row[f'{side}_fingerprint'],
            job=row[f'{side}_job'],
            trial=row[f'{side}_trial'],
        )
        for side in ('a', 'b')
    }
    return Vote(
        id=row['id'],
        session=row['session'],
        case=row['case_id'],
        choice=row['choice'],
        auto=row['auto'],
        flags=frozenset(json.loads(row['flags'])),
        note=row['note'],
        duration_sec=row['duration_sec'],
        repeat_of=row['repeat_of'],
        recorded_at=datetime.fromisoformat(row['recorded_at']),
        **sides,
    )
