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

# Ties and both-bad votes count as half a win for each side.
_LEFT_SCORE: dict[Choice, float] = {'a': 1.0, 'b': 0.0, 'tie': 0.5, 'both_bad': 0.5}

SCHEMA_VERSION = 1

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
  flags TEXT NOT NULL,
  note TEXT,
  duration_sec REAL NOT NULL,
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
# `INSERT OR REPLACE` deletes the row it replaces without firing DELETE triggers.
_NO_REPLACE = """
CREATE TRIGGER IF NOT EXISTS {table}_no_replace BEFORE INSERT ON {table}
WHEN EXISTS (SELECT 1 FROM {table} WHERE {key} = NEW.{key})
BEGIN SELECT RAISE(ABORT, 'the vote store is append-only'); END;
"""
_KEYS = {'sessions': 'id', 'votes': 'id', 'undos': 'vote'}
_VOTE_COLUMNS = (
    'session',
    'case_id',
    *(
        f'{side}_{key}'
        for side in 'ab'
        for key in ('system', 'fingerprint', 'job', 'trial')
    ),
    'choice',
    'flags',
    'note',
    'duration_sec',
    'repeat_of',
    'recorded_at',
)


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
    """An owner's vote to record, with side A shown on the left.

    `duration_sec` runs from serving the pair to receiving the vote, and
    `repeat_of` names the vote that a side-swapped repeat repeats.
    """

    model_config = ConfigDict(frozen=True)

    session: str
    case: str
    a: Side
    b: Side
    choice: Choice
    flags: frozenset[Flag] = frozenset()
    note: str | None = None
    duration_sec: float
    repeat_of: int | None = None


class Vote(Ballot):
    id: int
    recorded_at: datetime

    def comparison(self) -> Comparison:
        return Comparison(
            self.a.system, self.b.system, _LEFT_SCORE[self.choice], self.case
        )


class VoteStore:
    """Sessions and votes in SQLite, where an undo is a row of its own.

    Raises
    ------
    ValueError
        The file holds a schema version other than `SCHEMA_VERSION`.
    """

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # The API's handlers share one connection on the event loop's thread, which
        # need not be the thread that opened it.
        self._db = sqlite3.connect(path, autocommit=True, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute('PRAGMA foreign_keys = ON')
        (version,) = self._db.execute('PRAGMA user_version').fetchone()
        if version == 0:
            self._create()
        elif version != SCHEMA_VERSION:
            self._db.close()
            raise ValueError(
                f'{path} has schema version {version}, '
                f'while this bench reads version {SCHEMA_VERSION}'
            )

    def _create(self) -> None:
        self._db.executescript(_SCHEMA)
        for table, key in _KEYS.items():
            for action in ('UPDATE', 'DELETE'):
                self._db.execute(_APPEND_ONLY.format(table=table, action=action))
            self._db.execute(_NO_REPLACE.format(table=table, key=key))
        self._db.execute(f'PRAGMA user_version = {SCHEMA_VERSION}')

    def close(self) -> None:
        self._db.close()

    def start_session(self, family: str, at: datetime) -> Session:
        session = Session(id=uuid.uuid4().hex, family=family, started_at=at)
        self._db.execute(
            'INSERT INTO sessions (id, family, started_at) VALUES (?, ?, ?)',
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
            f'INSERT INTO votes ({", ".join(_VOTE_COLUMNS)}) '
            f'VALUES ({", ".join("?" * len(_VOTE_COLUMNS))})',
            (
                ballot.session,
                ballot.case,
                *(getattr(ballot.a, key) for key in Side.model_fields),
                *(getattr(ballot.b, key) for key in Side.model_fields),
                ballot.choice,
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
        self._db.execute(
            'INSERT INTO undos (vote, undone_at) VALUES (?, ?)',
            (vote_id, at.isoformat()),
        )

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
        flags=frozenset(json.loads(row['flags'])),
        note=row['note'],
        duration_sec=row['duration_sec'],
        repeat_of=row['repeat_of'],
        recorded_at=datetime.fromisoformat(row['recorded_at']),
        **sides,
    )
