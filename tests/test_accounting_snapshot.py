import asyncio
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from threading import Barrier
from typing import cast

import pytest

from assistant_bench.database import Param, Result, SQLiteDatabase
from assistant_bench.harnesses.accounting import RequestReceipt
from assistant_bench.harnesses.usage import MeasuredUsage
from assistant_bench.spend import Entry, PendingAttempt, read_ledger
from assistant_bench.state import AttemptStart, RequestAdmission, State
from support import START, accounted_trial, private_database


class FinalizeAfterRead:
    def __init__(
        self,
        reader: SQLiteDatabase,
        writer: State,
        claim_id: str,
        entry: Entry,
    ) -> None:
        self.reader = reader
        self.writer = writer
        self.claim_id = claim_id
        self.entry = entry
        self.finished = False

    def execute(self, sql: str, params: Sequence[Param] = ()) -> Result:
        result = self.reader.execute(sql, params)
        if not self.finished:
            self.finished = True
            self.writer.record_entry(self.claim_id, self.entry.model_dump_json())
        return result


class TestAccountingSnapshot:
    @pytest.mark.parametrize(
        'usage', [MeasuredUsage(100, 20, 0, 5, None), None], ids=['known', 'unknown']
    )
    def test_concurrent_finalization_neither_loses_nor_duplicates_a_response(
        self, tmp_path: Path, usage: MeasuredUsage | None
    ) -> None:
        with (
            private_database(tmp_path / 'state.sqlite') as reader,
            private_database(tmp_path / 'state.sqlite') as writer,
        ):
            state = State(writer)
            with accounted_trial(state) as recorder:
                recorder.begin(START)
                request = asyncio.run(recorder.admit('openai-responses'))
                asyncio.run(
                    recorder.confirm(
                        request,
                        RequestReceipt(
                            api='openai-responses',
                            outcome='completed',
                            http_status=200,
                            usage=usage,
                        ),
                    )
                )
                recorder.finish(START + timedelta(seconds=1))
                attempt = recorder.attempt()
                assert attempt is not None
                owner = state.get_claim(recorder.claim_id)
                run = state.run(owner.run_id)
                entry = Entry(
                    **attempt.model_dump(), job=run.job, price=None, ceiling_usd=None
                )
                database = FinalizeAfterRead(reader, state, recorder.claim_id, entry)

                during = read_ledger('sample', State(database))
                after = read_ledger('sample', State(reader))

                assert during == [
                    PendingAttempt(
                        job=run.job,
                        trial='trial',
                        system='acme-large',
                        price=None,
                        ceiling_usd=None,
                        responses=[usage],
                    )
                ]
                assert after == [entry]


class TestBoundedAdmission:
    def test_two_clients_compete_for_the_last_request_across_two_claims(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / 'state.sqlite'
        with private_database(path) as database:
            state = State(database)
            with accounted_trial(state, max_requests=2) as recorder:
                recorder.begin(START)
                first = asyncio.run(recorder.admit('openai-responses'))
                asyncio.run(
                    recorder.confirm(
                        first,
                        RequestReceipt(
                            api='openai-responses',
                            outcome='http_error',
                            http_status=503,
                            usage=None,
                        ),
                    )
                )
                owner = state.get_claim(recorder.claim_id)
                second = replace(
                    owner, id='second-claim', slot_key='second-slot', trial='second'
                )
                assert state.claim_attempt(second)
                assert state.begin_attempt(
                    AttemptStart('second-attempt', second.id, START.isoformat())
                )
                (initial,) = state.requests('sample')
                attempts = [cast(str, initial['attempt_id']), 'second-attempt']
                barrier = Barrier(2)

                def compete(index: int) -> bool:
                    with private_database(path) as client:
                        barrier.wait(timeout=5)
                        return State(client).admit_request(
                            RequestAdmission(
                                f'contender-{index}', attempts[index], START.isoformat()
                            )
                        )

                with ThreadPoolExecutor(max_workers=2) as clients:
                    granted = list(clients.map(compete, range(2)))

                assert sorted(granted) == [False, True]
                admitted = {
                    cast(str, row['request_id']) for row in state.requests('sample')
                }
                assert admitted == {first, f'contender-{granted.index(True)}'}
                assert not state.admit_request(
                    RequestAdmission('later-request', attempts[0], START.isoformat())
                )
