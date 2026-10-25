"""Candidate that commits rows before updating their checkpoint."""

import hashlib
from pathlib import Path

from ledgerload.csv_input import records
from ledgerload.models import ConflictError, ImportError, ImportResult
from ledgerload.store import Store


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
            raise ImportError(0, 'Source bytes changed.')

        with store.connection:
            store.connection.execute(
                'INSERT OR IGNORE INTO sources (source_id, sha256) VALUES (?, ?)',
                (source_id, digest),
            )

        checkpoint = store.checkpoint(source_id)
        remaining = (
            (index, row) for index, row in records(content) if index > checkpoint
        )
        while True:
            batch_processed = batch_inserted = 0
            with store.connection:
                for _ in range(batch_size):
                    try:
                        index, row = next(remaining)
                    except StopIteration:
                        break

                    existing = store.connection.execute(
                        'SELECT amount_cents, memo FROM ledger WHERE external_id = ?',
                        (row.external_id,),
                    ).fetchone()
                    if existing is not None and existing != (
                        row.amount_cents,
                        row.memo,
                    ):
                        raise ConflictError(
                            index, 'External ID has a different payload.'
                        )

                    if existing is None:
                        store.connection.execute(
                            'INSERT INTO ledger (external_id, amount_cents, memo) '
                            'VALUES (?, ?, ?)',
                            (row.external_id, row.amount_cents, row.memo),
                        )
                        batch_inserted += 1

                    checkpoint = index
                    batch_processed += 1

            if batch_processed:
                with store.connection:
                    store.connection.execute(
                        'UPDATE sources SET checkpoint = ? WHERE source_id = ?',
                        (checkpoint, source_id),
                    )

            processed += batch_processed
            inserted += batch_inserted
            if batch_processed < batch_size:
                break

        return ImportResult(processed, inserted, checkpoint)
