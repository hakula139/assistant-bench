"""Import source records into the persistent ledger."""

import hashlib
from pathlib import Path

from .csv_input import records
from .models import ConflictError, ImportResult, SourceError
from .store import Store


def import_file(
    db: Path, csv_path: Path, *, source_id: str, batch_size: int = 100
) -> ImportResult:
    if type(batch_size) is not int or batch_size < 1:
        raise ValueError('Batch size must be a positive integer.')

    if not source_id:
        raise ValueError('Source ID must be nonempty.')

    content = csv_path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    processed = inserted = 0
    with Store(db) as store:
        original = store.source_digest(source_id)
        if original is not None and original != digest:
            raise SourceError(0, 'Source bytes changed.')

        with store.connection:
            store.connection.execute(
                'INSERT OR IGNORE INTO sources (source_id, sha256) VALUES (?, ?)',
                (source_id, digest),
            )

        for index, row in records(content):
            existing = store.connection.execute(
                'SELECT amount_cents, memo FROM ledger WHERE external_id = ?',
                (row.external_id,),
            ).fetchone()
            if existing is not None and existing != (row.amount_cents, row.memo):
                raise ConflictError(index, 'External ID has a different payload.')

            with store.connection:
                if existing is None:
                    store.connection.execute(
                        'INSERT INTO ledger VALUES (?, ?, ?)',
                        (row.external_id, row.amount_cents, row.memo),
                    )
                    inserted += 1

            with store.connection:
                store.connection.execute(
                    'UPDATE sources SET checkpoint = ? WHERE source_id = ?',
                    (index, source_id),
                )
            processed += 1

        return ImportResult(processed, inserted, store.checkpoint(source_id))
