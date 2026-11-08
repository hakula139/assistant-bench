import asyncio
import json
from collections.abc import Generator
from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from assistant_bench import cli
from assistant_bench.canaries import Record
from assistant_bench.catalog import SystemIdentity
from assistant_bench.database import SQLiteDatabase
from assistant_bench.harnesses import fingerprint
from assistant_bench.harnesses.accounting import RequestReceipt
from assistant_bench.harnesses.usage import MeasuredUsage
from assistant_bench.spend import Entry
from assistant_bench.state import State
from support import START, accounted_trial, make_system


class TestResetCanary:
    def test_clears_only_the_named_systems_contexts_and_preserves_history(
        self,
        private_store: SQLiteDatabase,
        private_state: State,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        pointers: list[tuple[str, str, str]] = []
        for index, name in enumerate(('model_a', 'model_a', 'modelXa')):
            system = make_system(name=name)
            label = fingerprint(system)
            identity = SystemIdentity.model_validate(system.model_dump())
            usage = MeasuredUsage(100, 20, 0, 5, None)
            with accounted_trial(
                private_state, system=name, fingerprint=label, scope='canaries'
            ) as recorder:
                recorder.begin(START + timedelta(seconds=index))
                request = asyncio.run(recorder.admit('openai-chat-completions'))
                asyncio.run(
                    recorder.confirm(
                        request,
                        RequestReceipt(
                            api='openai-chat-completions',
                            outcome='completed',
                            http_status=200,
                            usage=usage,
                        ),
                    )
                )
                recorder.finish(START + timedelta(seconds=index + 1))
                attempt = recorder.attempt()
                assert attempt is not None
                claim = private_state.get_claim(recorder.claim_id)
                parent = private_state.run(claim.run_id)
                entry = Entry(
                    **attempt.model_dump(), job=parent.job, price=None, ceiling_usd=None
                )
                private_state.record_entry(claim.id, entry.model_dump_json())
                record = Record(
                    system=name,
                    job=parent.job,
                    trial=claim.trial,
                    time=attempt.started_at,
                    original_identity=identity,
                    source_generate_identity=identity,
                    actual_probe_identity=identity,
                    source_generate_fingerprint=label,
                    actual_probe_fingerprint=label,
                    probe_protocol_digest=f'{index:064x}',
                    usage=usage,
                    response_models=['model-snapshot'],
                    output='READY',
                    finish_reason='stop',
                    latency_sec=0.1,
                    error=None,
                )
                private_state.append_canary(parent.id, record.model_dump_json())
                key = json.dumps([label, record.probe_protocol_digest])
                assert private_state.establish_baseline(name, key, parent.id)
                pointers.append((name, key, record.model_dump_json()))
        tables = (
            'canary_records',
            'attempt_entries',
            'request_admissions',
            'request_receipts',
        )
        history = {
            table: private_store.execute(f'SELECT * FROM {table} ORDER BY rowid').rows
            for table in tables
        }

        @contextmanager
        def connect(target: str) -> Generator[SQLiteDatabase]:
            assert target == 'private'
            yield private_store

        monkeypatch.setattr(cli, 'connect', connect)
        materialize = Mock(side_effect=AssertionError('Reset materialized sources.'))
        monkeypatch.setattr(cli, 'open_context', materialize)

        result = CliRunner().invoke(cli.app, ['reset-canary', 'model_a'])
        assert result.exit_code == 0, result.output

        assert (
            result.output == 'System `model_a`: cleared 2 canary baseline contexts.\n'
        )
        for name, key, value in pointers:
            assert private_state.baseline(name, key) == (
                None if name == 'model_a' else value
            )
        assert {
            table: private_store.execute(f'SELECT * FROM {table} ORDER BY rowid').rows
            for table in tables
        } == history
        repeated = CliRunner().invoke(cli.app, ['reset-canary', 'model_a'])
        assert repeated.exit_code == 0, repeated.output
        assert (
            repeated.output == 'System `model_a`: cleared 0 canary baseline contexts.\n'
        )
        materialize.assert_not_called()
