"""Private run state and immutable records shared by disposable clients."""

import hashlib
import json
from dataclasses import dataclass, replace
from typing import Literal, cast

from pydantic import JsonValue

from assistant_bench.database import Database, Row
from assistant_bench.messages import InputError


type ClaimState = Literal['claimed', 'uncertain', 'settled']
type RunStatus = Literal['prepared', 'running', 'complete', 'uncertain']


@dataclass(frozen=True)
class Run:
    id: str
    scope: str
    job: str
    code_revision: str
    case_revision: str
    created_at: str


@dataclass(frozen=True)
class Claim:
    id: str
    run_id: str
    scope: str
    slot_key: str
    trial: str
    system: str
    fingerprint: str
    accounting_json: str
    expected_source_revision: int
    created_at: str


@dataclass(frozen=True)
class Collection:
    id: str
    family: str
    expected_count: int
    manifest_digest: str
    created_at: str


class State:
    """Conditional claims and immutable domain-validated JSON records.

    JSON inputs come from their owning models. This layer preserves their bytes
    and frozen accounting without calculating prices or interpreting outcomes.
    """

    def __init__(self, database: Database) -> None:
        self._database = database

    # ── Runs and Paid Claims ──

    def create_run(self, run: Run) -> None:
        self._database.execute(
            """
            INSERT INTO runs (id, scope, job, code_revision, case_revision, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (id) DO NOTHING
            """,
            (
                run.id,
                run.scope,
                run.job,
                run.code_revision,
                run.case_revision,
                run.created_at,
            ),
        )
        if self._run(run.id) != run:
            raise InputError(f'Run `{run.id}` already has different metadata.')

    def source_revision(self, scope: str) -> int:
        rows = self._database.execute(
            'SELECT revision FROM source_heads WHERE scope = ?', (scope,)
        ).rows
        return cast(int, rows[0]['revision']) if rows else 0

    def claim_attempt(self, claim: Claim) -> bool:
        """Claim a slot only at the source revision used to plan its generation.

        A repeated claim ID returns false, so it never admits another paid call.
        Claimed and uncertain slots remain occupied until their artifacts settle.
        """
        if _object(claim.accounting_json)['system'] != claim.system:
            raise InputError(f'Claim `{claim.id}` has a different accounting system.')
        rows = self._database.execute(
            """
            INSERT INTO attempt_claims (
                id, run_id, scope, slot_key, trial, system, fingerprint,
                accounting_json, expected_source_revision, created_at
            )
            SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            WHERE EXISTS (
                SELECT 1 FROM runs
                WHERE id = ? AND scope = ? AND status IN ('prepared', 'running')
            ) AND COALESCE((SELECT revision FROM source_heads WHERE scope = ?), 0) = ?
            ON CONFLICT DO NOTHING
            RETURNING id
            """,
            (
                claim.id,
                claim.run_id,
                claim.scope,
                claim.slot_key,
                claim.trial,
                claim.system,
                claim.fingerprint,
                claim.accounting_json,
                claim.expected_source_revision,
                claim.created_at,
                claim.run_id,
                claim.scope,
                claim.scope,
                claim.expected_source_revision,
            ),
        ).rows
        if rows:
            return True

        existing = self._database.execute(
            'SELECT * FROM attempt_claims WHERE id = ?', (claim.id,)
        ).rows
        if existing:
            previous = _claim(existing[0])
            if replace(
                previous, accounting_json=claim.accounting_json
            ) != claim or not _same_json(
                previous.accounting_json, claim.accounting_json
            ):
                raise InputError(f'Claim `{claim.id}` already has different metadata.')
        return False

    def get_claim(self, claim_id: str) -> Claim:
        return _claim(self._claim_row(claim_id))

    def claim_state(self, claim_id: str) -> ClaimState:
        return cast(ClaimState, self._claim_row(claim_id)['state'])

    def mark_uncertain(self, claim_id: str) -> None:
        self._claim_row(claim_id)
        self._database.execute(
            "UPDATE attempt_claims SET state = 'uncertain' "
            "WHERE id = ? AND state = 'claimed'",
            (claim_id,),
        )

    def record_entry(self, claim_id: str, entry_json: str) -> None:
        """Append one already-priced Entry without releasing its paid slot."""
        claim = self.get_claim(claim_id)
        run = self._run(claim.run_id)
        entry = _object(entry_json)
        accounting = _object(claim.accounting_json)
        expected = {
            'job': run.job,
            'trial': claim.trial,
            'system': claim.system,
            'fingerprint': claim.fingerprint,
            'price': accounting['price'],
            'ceiling_usd': accounting['ceiling_usd'],
        }
        if any(entry[name] != value for name, value in expected.items()):
            raise InputError(
                f'Entry for claim `{claim_id}` differs from its frozen '
                'identity or accounting.'
            )

        started_at = cast(str, entry['started_at'])
        self._database.execute(
            """
            INSERT INTO attempt_entries (
                scope, job, trial, started_at, claim_id, entry_json
            )
            SELECT claims.scope, runs.job, claims.trial, ?, claims.id, ?
            FROM attempt_claims AS claims JOIN runs ON runs.id = claims.run_id
            WHERE claims.id = ? AND claims.state IN ('claimed', 'uncertain')
            ON CONFLICT (scope, job, trial, started_at) DO NOTHING
            """,
            (started_at, entry_json, claim_id),
        )
        rows = self._database.execute(
            """
            SELECT claim_id, entry_json FROM attempt_entries
            WHERE scope = ? AND job = ? AND trial = ? AND started_at = ?
            """,
            (claim.scope, run.job, claim.trial, started_at),
        ).rows
        if (
            not rows
            or rows[0]['claim_id'] != claim_id
            or not _same_json(cast(str, rows[0]['entry_json']), entry_json)
        ):
            raise InputError(
                f'Claim `{claim_id}` cannot record a different or settled attempt.'
            )

    def entries(self, scope: str) -> list[str]:
        return [
            cast(str, row['entry_json'])
            for row in self._database.execute(
                'SELECT entry_json FROM attempt_entries WHERE scope = ? ORDER BY rowid',
                (scope,),
            ).rows
        ]

    def settle_claim(
        self, claim_id: str, commit_sha: str, path: str, manifest_digest: str
    ) -> None:
        """Attach a remotely verified artifact reference and atomically release a slot.

        The caller confirms remote Git durability first. The SQL insertion and
        its triggers require a ledger entry, settle the claim, and advance the
        scope's source counter together. Repeated matching references do neither
        another release nor another counter increment.
        """
        self._claim_row(claim_id)
        self._database.execute(
            """
            INSERT INTO claim_artifacts (claim_id, commit_sha, path, manifest_digest)
            SELECT id, ?, ?, ? FROM attempt_claims
            WHERE id = ? AND state IN ('claimed', 'uncertain')
                AND EXISTS (
                    SELECT 1 FROM attempt_entries
                    WHERE claim_id = attempt_claims.id
                )
            ON CONFLICT (claim_id) DO NOTHING
            """,
            (commit_sha, path, manifest_digest, claim_id),
        )
        rows = self._database.execute(
            'SELECT commit_sha, path, manifest_digest FROM claim_artifacts '
            'WHERE claim_id = ?',
            (claim_id,),
        ).rows
        if not rows:
            raise InputError(
                f'Claim `{claim_id}` needs a recorded attempt before settlement.'
            )
        if rows[0] != {
            'commit_sha': commit_sha,
            'path': path,
            'manifest_digest': manifest_digest,
        }:
            raise InputError(
                f'Claim `{claim_id}` already has different artifact references.'
            )

    def artifacts(self, scope: str) -> list[Row]:
        return self._database.execute(
            """
            SELECT artifacts.claim_id, claims.run_id, claims.trial,
                artifacts.commit_sha, artifacts.path, artifacts.manifest_digest
            FROM claim_artifacts AS artifacts
            JOIN attempt_claims AS claims ON claims.id = artifacts.claim_id
            WHERE claims.scope = ? ORDER BY claims.created_at, claims.id
            """,
            (scope,),
        ).rows

    def complete_run(self, run_id: str) -> bool:
        self._run(run_id)
        return bool(
            self._database.execute(
                """
            UPDATE runs SET status = 'complete'
            WHERE id = ? AND NOT EXISTS (
                SELECT 1 FROM attempt_claims AS claims
                LEFT JOIN claim_artifacts AS artifacts ON artifacts.claim_id = claims.id
                WHERE claims.run_id = runs.id
                    AND (claims.state != 'settled' OR artifacts.claim_id IS NULL)
            )
            RETURNING id
            """,
                (run_id,),
            ).rows
        )

    def run_status(self, run_id: str) -> RunStatus:
        return cast(RunStatus, self._run_row(run_id)['status'])

    # ── Output Generations ──

    def begin_collection(self, collection: Collection) -> None:
        self._database.execute(
            """
            INSERT INTO collections (
                id, family, expected_count, manifest_digest, created_at
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (id) DO NOTHING
            """,
            (
                collection.id,
                collection.family,
                collection.expected_count,
                collection.manifest_digest,
                collection.created_at,
            ),
        )
        if _collection(self._collection_row(collection.id)) != collection:
            raise InputError(
                f'Collection `{collection.id}` already has different metadata.'
            )

    def stage_output(self, collection: str, position: int, output_json: str) -> None:
        output = _object(output_json)
        key = json.dumps(
            [output[name] for name in ('case', 'system', 'job', 'trial')],
            ensure_ascii=False,
            separators=(',', ':'),
        )
        self._database.execute(
            """
            INSERT INTO collection_outputs (
                collection_id, position, output_key, output_json
            )
            SELECT ?, ?, ?, ? WHERE NOT EXISTS (
                SELECT 1 FROM collection_outputs
                WHERE collection_id = ? AND position = ?
            )
            """,
            (collection, position, key, output_json, collection, position),
        )
        rows = self._database.execute(
            """
            SELECT output_key, output_json FROM collection_outputs
            WHERE collection_id = ? AND position = ?
            """,
            (collection, position),
        ).rows
        if rows[0]['output_key'] != key or not _same_json(
            cast(str, rows[0]['output_json']), output_json
        ):
            raise InputError(
                f'Collection `{collection}` already has a different output there.'
            )

    def seal_collection(self, collection: str) -> None:
        row = self._collection_row(collection)
        if row['sealed'] == 1:
            return
        outputs = self._database.execute(
            """
            SELECT position, output_json FROM collection_outputs
            WHERE collection_id = ? ORDER BY position
            """,
            (collection,),
        ).rows
        digest = hashlib.sha256()
        for output in outputs:
            digest.update((cast(str, output['output_json']) + '\n').encode())
        count = cast(int, row['expected_count'])
        if (
            len(outputs) != count
            or [item['position'] for item in outputs] != list(range(count))
            or digest.hexdigest() != row['manifest_digest']
        ):
            raise InputError(
                f'Collection `{collection}` does not match its complete manifest.'
            )

        sealed = self._database.execute(
            """
            UPDATE collections SET sealed = 1
            WHERE id = ? AND sealed = 0
                AND (
                    SELECT count(*) FROM collection_outputs WHERE collection_id = ?
                ) = expected_count
            RETURNING id
            """,
            (collection, collection),
        ).rows
        if not sealed and self._collection_row(collection)['sealed'] != 1:
            raise InputError(
                f'Collection `{collection}` changed before it could be sealed.'
            )

    def activate_collection(
        self, collection: str, expected_previous: str | None
    ) -> bool:
        row = self._collection_row(collection)
        if row['sealed'] != 1:
            raise InputError(
                f'Collection `{collection}` must be sealed before activation.'
            )
        family = cast(str, row['family'])
        if expected_previous is None:
            changed = self._database.execute(
                """
                INSERT INTO active_collections (family, collection_id) VALUES (?, ?)
                ON CONFLICT (family) DO NOTHING RETURNING collection_id
                """,
                (family, collection),
            ).rows
        else:
            changed = self._database.execute(
                """
                UPDATE active_collections SET collection_id = ?
                WHERE family = ? AND collection_id = ? RETURNING collection_id
                """,
                (collection, family, expected_previous),
            ).rows
        if changed:
            return True
        current = self._database.execute(
            'SELECT collection_id FROM active_collections WHERE family = ?', (family,)
        ).rows
        return bool(current and current[0]['collection_id'] == collection)

    def outputs(self, family: str) -> list[str]:
        return [
            cast(str, row['output_json'])
            for row in self._database.execute(
                """
                SELECT outputs.output_json FROM collection_outputs AS outputs
                JOIN active_collections AS active
                    ON active.collection_id = outputs.collection_id
                WHERE active.family = ? ORDER BY outputs.position
                """,
                (family,),
            ).rows
        ]

    # ── Canary History and Baselines ──

    def append_canary(self, run: str, record_json: str) -> None:
        record = _object(record_json)
        self._database.execute(
            """
            INSERT INTO canary_records (run_id, system, record_json) VALUES (?, ?, ?)
            ON CONFLICT (run_id) DO NOTHING
            """,
            (run, cast(str, record['system']), record_json),
        )
        rows = self._database.execute(
            'SELECT record_json FROM canary_records WHERE run_id = ?', (run,)
        ).rows
        if not _same_json(cast(str, rows[0]['record_json']), record_json):
            raise InputError(f'Canary run `{run}` already has a different outcome.')

    def records(self, system: str) -> list[str]:
        return [
            cast(str, row['record_json'])
            for row in self._database.execute(
                """
                SELECT record_json FROM canary_records WHERE system = ?
                ORDER BY json_extract(record_json, '$.time'), run_id
                """,
                (system,),
            ).rows
        ]

    def baseline(self, system: str) -> str | None:
        rows = self._database.execute(
            """
            SELECT records.record_json FROM canary_records AS records
            JOIN canary_baselines AS baselines ON baselines.run_id = records.run_id
            WHERE baselines.system = ?
            """,
            (system,),
        ).rows
        return cast(str, rows[0]['record_json']) if rows else None

    def establish_baseline(self, system: str, run: str) -> bool:
        """Keep the first baseline the caller has accepted as successful."""
        return bool(
            self._database.execute(
                """
            INSERT INTO canary_baselines (system, run_id)
            SELECT system, run_id FROM canary_records WHERE system = ? AND run_id = ?
            ON CONFLICT (system) DO NOTHING RETURNING run_id
            """,
                (system, run),
            ).rows
        )

    def reset_baseline(self, system: str) -> None:
        self._database.execute(
            'DELETE FROM canary_baselines WHERE system = ?', (system,)
        )

    # ── Stored Metadata ──

    def _run_row(self, run_id: str) -> Row:
        rows = self._database.execute('SELECT * FROM runs WHERE id = ?', (run_id,)).rows
        if not rows:
            raise InputError(f'Run `{run_id}` is not recorded.')
        return rows[0]

    def _run(self, run_id: str) -> Run:
        row = self._run_row(run_id)
        return Run(
            id=cast(str, row['id']),
            scope=cast(str, row['scope']),
            job=cast(str, row['job']),
            code_revision=cast(str, row['code_revision']),
            case_revision=cast(str, row['case_revision']),
            created_at=cast(str, row['created_at']),
        )

    def _claim_row(self, claim_id: str) -> Row:
        rows = self._database.execute(
            'SELECT * FROM attempt_claims WHERE id = ?', (claim_id,)
        ).rows
        if not rows:
            raise InputError(f'Claim `{claim_id}` is not recorded.')
        return rows[0]

    def _collection_row(self, collection: str) -> Row:
        rows = self._database.execute(
            'SELECT * FROM collections WHERE id = ?', (collection,)
        ).rows
        if not rows:
            raise InputError(f'Collection `{collection}` is not recorded.')
        return rows[0]


def _claim(row: Row) -> Claim:
    return Claim(
        id=cast(str, row['id']),
        run_id=cast(str, row['run_id']),
        scope=cast(str, row['scope']),
        slot_key=cast(str, row['slot_key']),
        trial=cast(str, row['trial']),
        system=cast(str, row['system']),
        fingerprint=cast(str, row['fingerprint']),
        accounting_json=cast(str, row['accounting_json']),
        expected_source_revision=cast(int, row['expected_source_revision']),
        created_at=cast(str, row['created_at']),
    )


def _collection(row: Row) -> Collection:
    return Collection(
        id=cast(str, row['id']),
        family=cast(str, row['family']),
        expected_count=cast(int, row['expected_count']),
        manifest_digest=cast(str, row['manifest_digest']),
        created_at=cast(str, row['created_at']),
    )


def _object(text: str) -> dict[str, JsonValue]:
    return cast(dict[str, JsonValue], json.loads(text))


def _same_json(first: str, second: str) -> bool:
    return _object(first) == _object(second)
