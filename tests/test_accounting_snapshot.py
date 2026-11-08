import asyncio
from collections.abc import Callable, Sequence
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
from assistant_bench.state import (
    ArtifactGeneration,
    AttemptStart,
    RequestAdmission,
    Run,
    State,
    TrialArtifact,
)
from support import START, accounted_trial, private_database


class CommitAfterRead:
    def __init__(
        self,
        reader: SQLiteDatabase,
        commit: Callable[[], None],
    ) -> None:
        self.reader = reader
        self.commit = commit
        self.finished = False

    def execute(self, sql: str, params: Sequence[Param] = ()) -> Result:
        result = self.reader.execute(sql, params)
        if not self.finished:
            self.finished = True
            self.commit()
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
                database = CommitAfterRead(
                    reader,
                    lambda: state.record_entry(
                        recorder.claim_id, entry.model_dump_json()
                    ),
                )

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

    def test_artifact_inventory_and_revision_cannot_straddle_registration(
        self, tmp_path: Path
    ) -> None:
        with (
            private_database(tmp_path / 'state.sqlite') as reader,
            private_database(tmp_path / 'state.sqlite') as writer,
        ):
            state = State(writer)
            state.create_run(
                Run('run-a', 'sample', 'job-a', '1' * 40, '2' * 40, START.isoformat())
            )
            artifact = TrialArtifact(
                run_id='run-a',
                trial_id='trial-a',
                source_trial_id='source-trial-a',
                commit_sha='a' * 40,
                path='runs/run-a/trials/artifact-a',
                manifest_digest='b' * 64,
            )

            def register() -> None:
                state.record_trial_artifact(
                    artifact.run_id,
                    artifact.trial_id,
                    artifact.source_trial_id,
                    artifact.commit_sha,
                    artifact.path,
                    artifact.manifest_digest,
                )

            during = State(CommitAfterRead(reader, register)).artifact_generation(
                'sample'
            )
            after = State(reader).artifact_generation('sample')

            assert during == ArtifactGeneration(revision=0, artifacts=())
            assert after == ArtifactGeneration(revision=1, artifacts=(artifact,))


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
