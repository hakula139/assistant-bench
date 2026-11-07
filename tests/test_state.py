import hashlib
import json
import sqlite3
from collections.abc import Generator, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from dataclasses import replace
from datetime import timedelta
from importlib.resources import files
from pathlib import Path
from threading import Barrier

import pytest

from assistant_bench.canaries import Record
from assistant_bench.catalog import Price, SystemIdentity
from assistant_bench.database import SQLiteDatabase
from assistant_bench.harnesses.usage import MeasuredUsage
from assistant_bench.messages import InputError
from assistant_bench.spend import CanaryAccounting, Entry
from assistant_bench.state import (
    AttemptStart,
    Claim,
    Collection,
    RequestAdmission,
    Run,
    State,
)
from support import START, make_output, make_system


PRICE = Price(input=2, output=10, cache_read=0.2, cache_write=2.5)
COMMIT = 'a' * 40
DIGEST = 'b' * 64


@contextmanager
def connect(path: Path) -> Generator[State]:
    with closing(sqlite3.connect(path, autocommit=True)) as connection:
        connection.execute('PRAGMA foreign_keys = ON')
        yield State(SQLiteDatabase(connection))


@pytest.fixture
def state(tmp_path: Path) -> Iterator[State]:
    path = tmp_path / 'state.sqlite'
    schema = files('assistant_bench').joinpath('sql/private/0001_state.sql')
    with closing(sqlite3.connect(path, autocommit=True)) as connection:
        connection.executescript(schema.read_text())
    with connect(path) as store:
        yield store


def run(identifier: str = 'run-a', *, scope: str = 'sample') -> Run:
    return Run(
        id=identifier,
        scope=scope,
        job=identifier,
        code_revision='1' * 40,
        case_revision='2' * 40,
        created_at=START.isoformat(),
    )


def claim(
    parent: Run,
    identifier: str = 'claim-a',
    *,
    slot: str = 'task-v1/model-a/sample-0',
    revision: int = 0,
) -> Claim:
    return Claim(
        id=identifier,
        run_id=parent.id,
        scope=parent.scope,
        slot_key=slot,
        trial=identifier,
        system='model-a',
        fingerprint='f' * 12,
        accounting_json=CanaryAccounting(
            system='model-a', price=PRICE, ceiling_usd=0.35
        ).model_dump_json(),
        expected_source_revision=revision,
        created_at=START.isoformat(),
    )


def entry(parent: Run, owner: Claim, *, attempt: int = 0) -> Entry:
    start = START + timedelta(seconds=attempt)
    return Entry(
        job=parent.job,
        trial=owner.trial,
        system=owner.system,
        fingerprint=owner.fingerprint,
        started_at=start,
        finished_at=start + timedelta(milliseconds=100),
        responses=[MeasuredUsage(100, 20, 0, 5, None)],
        price=PRICE,
        ceiling_usd=0.35,
    )


def claimed(state: State, identifier: str = 'claim-a') -> tuple[Run, Claim]:
    parent = run()
    owner = claim(parent, identifier)
    state.create_run(parent)
    assert state.claim_attempt(owner)
    return parent, owner


def start(owner: Claim, number: int = 0) -> AttemptStart:
    return AttemptStart(
        id=f'attempt-{owner.id}-{number}',
        claim_id=owner.id,
        started_at=(START + timedelta(seconds=number))
        .isoformat()
        .replace('+00:00', 'Z'),
    )


def admission(attempt: AttemptStart, identifier: str = 'request-a') -> RequestAdmission:
    return RequestAdmission(identifier, attempt.id, attempt.started_at)


def register(state: State, parent: Run, owner: Claim) -> None:
    state.record_trial_artifact(
        parent.id,
        owner.trial,
        owner.trial,
        COMMIT,
        f'runs/{parent.id}/{owner.id}',
        DIGEST,
    )


def settled(state: State, parent: Run, owner: Claim) -> None:
    assert state.begin_attempt(start(owner))
    state.record_entry(owner.id, entry(parent, owner).model_dump_json())
    register(state, parent, owner)
    state.settle_claim(owner.id)


def output(label: str) -> str:
    return make_output(
        case=label,
        system='model-a',
        job='run-a',
        trial=label,
        text=f'Answer for {label}.\r\n',
    ).model_dump_json()


def collection(identifier: str, outputs: list[str]) -> Collection:
    return Collection(
        id=identifier,
        family='sample',
        expected_count=len(outputs),
        manifest_digest=hashlib.sha256(
            ''.join(f'{item}\n' for item in outputs).encode()
        ).hexdigest(),
        created_at=START.isoformat(),
    )


def sealed(state: State, identifier: str, *outputs: str) -> None:
    state.begin_collection(collection(identifier, list(outputs)))
    for position, item in enumerate(outputs):
        state.stage_output(identifier, position, item)
    state.seal_collection(identifier)


def canary(*, job: str = 'run-a', error: str | None = None) -> Record:
    identity = SystemIdentity.model_validate(make_system().model_dump())
    return Record(
        system='model-a',
        job=job,
        trial='trial-a',
        time=START,
        original_identity=identity,
        source_generate_identity=identity,
        actual_probe_identity=identity,
        source_generate_fingerprint='c' * 12,
        actual_probe_fingerprint='d' * 12,
        probe_protocol_digest='e' * 64,
        usage=MeasuredUsage(100, 20, 0, 5, None),
        response_models=['model-a'],
        output='Answer.\r\n',
        finish_reason='stop',
        latency_sec=0.1,
        error=error,
    )


class TestState:
    class TestCreateRun:
        @pytest.mark.parametrize('known', [True, False], ids=['pinned', 'unknown'])
        def test_preserves_original_provenance_without_replacing_unknown_revisions(
            self, state: State, known: bool
        ) -> None:
            parent = (
                run()
                if known
                else replace(run(), code_revision=None, case_revision=None)
            )
            state.create_run(parent)
            state.create_run(parent)

            with pytest.raises(InputError):
                state.create_run(replace(parent, case_revision='3' * 40))

            state.create_run(parent)

    class TestClaimAttempt:
        def test_two_connections_cannot_both_start_the_same_paid_slot(
            self, state: State, tmp_path: Path
        ) -> None:
            parent = run()
            state.create_run(parent)
            barrier = Barrier(2)

            def start(identifier: str) -> bool:
                with connect(tmp_path / 'state.sqlite') as writer:
                    barrier.wait()
                    return writer.claim_attempt(claim(parent, identifier))

            with ThreadPoolExecutor(max_workers=2) as workers:
                results = list(workers.map(start, ['claim-a', 'claim-b']))

            assert sorted(results) == [False, True]
            winner = 'claim-a' if results[0] else 'claim-b'
            assert state.get_claim(winner) == claim(parent, winner)
            assert state.claim_state(winner) == 'claimed'
            assert state.run_status(parent.id) == 'running'

        def test_uncertain_claim_keeps_its_slot_and_accounting(
            self, state: State
        ) -> None:
            parent, owner = claimed(state)
            state.mark_uncertain(owner.id)

            accepted = state.claim_attempt(claim(parent, 'claim-b'))

            assert not accepted
            assert state.claim_state(owner.id) == 'uncertain'
            assert state.get_claim(owner.id).accounting_json == owner.accounting_json
            assert state.run_status(parent.id) == 'uncertain'

        def test_repeating_a_claim_never_authorizes_another_paid_attempt(
            self, state: State
        ) -> None:
            _, owner = claimed(state)

            accepted = state.claim_attempt(owner)

            assert not accepted
            assert state.get_claim(owner.id) == owner

    class TestCancelClaim:
        def test_cancels_work_before_execution_without_inventing_a_completed_trial(
            self, state: State
        ) -> None:
            parent, owner = claimed(state)

            assert state.cancel_claim(owner.id)

            assert not state.cancel_claim(owner.id)
            assert not state.begin_attempt(start(owner))
            assert state.claim_state(owner.id) == 'cancelled'
            assert state.entries(parent.scope) == []
            assert state.artifacts(parent.scope) == []
            assert state.requests(parent.scope) == []
            assert state.source_revision(parent.scope) == 0
            assert state.complete_run(parent.id)
            following = run('run-next')
            state.create_run(following)
            assert state.claim_attempt(claim(following, 'claim-next'))

        def test_cancellation_and_start_cannot_both_win_on_two_connections(
            self, state: State, tmp_path: Path
        ) -> None:
            parent, owner = claimed(state)
            attempt = start(owner)
            barrier = Barrier(2)

            def compete(operation: str) -> bool:
                with connect(tmp_path / 'state.sqlite') as writer:
                    barrier.wait()
                    return (
                        writer.cancel_claim(owner.id)
                        if operation == 'cancel'
                        else writer.begin_attempt(attempt)
                    )

            with ThreadPoolExecutor(max_workers=2) as workers:
                cancelled, started = list(workers.map(compete, ['cancel', 'start']))

            assert sorted([cancelled, started]) == [False, True]
            assert state.claim_state(owner.id) == (
                'cancelled' if cancelled else 'claimed'
            )
            assert state.admit_request(admission(attempt)) == started
            assert state.source_revision(parent.scope) == 0

        def test_started_uncertain_work_keeps_its_slot_without_new_io_permission(
            self, state: State
        ) -> None:
            parent, owner = claimed(state)
            attempt = start(owner)
            assert state.begin_attempt(attempt)
            request = admission(attempt)
            assert state.admit_request(request)
            state.mark_uncertain(owner.id)

            assert not state.cancel_claim(owner.id)
            assert not state.begin_attempt(start(owner, 1))
            assert not state.admit_request(admission(attempt, 'another'))

            assert not state.claim_attempt(claim(parent, 'claim-b'))
            state.record_receipt(request.id, '{"usage":null}')
            state.record_entry(owner.id, entry(parent, owner).model_dump_json())
            assert state.claim_state(owner.id) == 'uncertain'
            assert not state.complete_run(parent.id)

    class TestBeginAttempt:
        def test_a_repeated_start_only_grants_one_permission_across_connections(
            self, state: State, tmp_path: Path
        ) -> None:
            _, owner = claimed(state)
            attempt = start(owner)
            barrier = Barrier(2)

            def begin(_: int) -> bool:
                with connect(tmp_path / 'state.sqlite') as writer:
                    barrier.wait()
                    return writer.begin_attempt(attempt)

            with ThreadPoolExecutor(max_workers=2) as workers:
                results = list(workers.map(begin, range(2)))

            assert sorted(results) == [False, True]
            assert not state.begin_attempt(attempt)
            with pytest.raises(InputError):
                state.begin_attempt(
                    replace(attempt, started_at=start(owner, 1).started_at)
                )

    class TestAdmitRequest:
        def test_repeated_request_id_cannot_authorize_another_send_after_reopening(
            self, state: State, tmp_path: Path
        ) -> None:
            parent, owner = claimed(state)
            attempt = start(owner)
            assert state.begin_attempt(attempt)
            request = admission(attempt)
            barrier = Barrier(2)

            def admit(_: int) -> bool:
                with connect(tmp_path / 'state.sqlite') as writer:
                    barrier.wait()
                    return writer.admit_request(request)

            with ThreadPoolExecutor(max_workers=2) as workers:
                results = list(workers.map(admit, range(2)))

            assert sorted(results) == [False, True]
            with connect(tmp_path / 'state.sqlite') as reopened:
                assert not reopened.admit_request(request)
            with pytest.raises(InputError):
                state.admit_request(replace(request, created_at='2025-01-01T00:00:00Z'))
            assert [row['request_id'] for row in state.requests(parent.scope)] == [
                request.id
            ]

        def test_an_entry_finalizes_only_its_own_attempt_admission_fence(
            self, state: State
        ) -> None:
            parent, owner = claimed(state)
            first, second = start(owner), start(owner, 1)
            assert state.begin_attempt(first)
            assert state.begin_attempt(second)
            assert state.admit_request(admission(first))
            state.record_entry(owner.id, entry(parent, owner).model_dump_json())

            assert not state.admit_request(admission(first, 'late'))
            assert state.admit_request(admission(second, 'retry'))

            assert [row['request_id'] for row in state.requests(parent.scope)] == [
                'request-a',
                'retry',
            ]

    class TestRecordReceipt:
        def test_pending_and_unknown_usage_stay_visible_without_double_counting_entries(
            self, state: State
        ) -> None:
            parent, owner = claimed(state)
            first, second = start(owner), start(owner, 1)
            assert state.begin_attempt(first)
            assert state.begin_attempt(second)
            for request in [
                admission(first),
                admission(first, 'unknown'),
                admission(second, 'retry'),
            ]:
                assert state.admit_request(request)
            unknown = '{"response":"response-a","usage":null}'
            state.record_receipt('unknown', unknown)
            state.record_receipt('unknown', json.dumps(json.loads(unknown)))
            with pytest.raises(InputError):
                state.record_receipt('unknown', '{"response":"response-a","usage":{}}')

            rows = state.requests(parent.scope, unrecorded_only=True)

            assert [(row['request_id'], row['receipt_json']) for row in rows] == [
                ('request-a', None),
                ('unknown', unknown),
                ('retry', None),
            ]
            assert all(row['accounting_json'] == owner.accounting_json for row in rows)
            assert json.loads(str(rows[1]['receipt_json']))['usage'] is None
            assert state.requests('another-scope') == []
            state.record_entry(owner.id, entry(parent, owner).model_dump_json())
            assert [
                row['request_id']
                for row in state.requests(parent.scope, unrecorded_only=True)
            ] == ['retry']
            assert state.requests(parent.scope) == rows

        @pytest.mark.parametrize('operation', ['UPDATE', 'DELETE'])
        def test_terminal_receipts_are_immutable_to_other_sql_writers(
            self, state: State, tmp_path: Path, operation: str
        ) -> None:
            parent, owner = claimed(state)
            attempt = start(owner)
            assert state.begin_attempt(attempt)
            request = admission(attempt)
            assert state.admit_request(request)
            state.record_receipt(request.id, '{"usage":null}')
            statement = (
                "UPDATE request_receipts SET receipt_json = '{}'"
                if operation == 'UPDATE'
                else 'DELETE FROM request_receipts'
            )

            with (
                closing(
                    sqlite3.connect(tmp_path / 'state.sqlite', autocommit=True)
                ) as other,
                pytest.raises(sqlite3.IntegrityError),
            ):
                other.execute(statement)

            assert state.requests(parent.scope)[0]['receipt_json'] == '{"usage":null}'

    class TestRecordEntry:
        def test_an_entry_requires_its_exact_recorded_start_before_acceptance(
            self, state: State
        ) -> None:
            parent, owner = claimed(state)
            original = entry(parent, owner)

            with pytest.raises(InputError):
                state.record_entry(owner.id, original.model_dump_json())

            assert state.entries(parent.scope) == []
            assert not state.admit_request(admission(start(owner)))
            assert state.begin_attempt(start(owner))
            state.record_entry(owner.id, original.model_dump_json())
            assert state.entries(parent.scope) == [original.model_dump_json()]

        def test_keeps_each_attempt_and_original_price_without_releasing_the_slot(
            self, state: State
        ) -> None:
            parent, owner = claimed(state)
            first = entry(parent, owner)
            second = entry(parent, owner, attempt=1)
            assert state.begin_attempt(start(owner))
            assert state.begin_attempt(start(owner, 1))

            state.record_entry(owner.id, first.model_dump_json())
            state.record_entry(owner.id, json.dumps(first.model_dump(mode='json')))
            state.record_entry(owner.id, second.model_dump_json())

            assert [
                Entry.model_validate_json(item) for item in state.entries('sample')
            ] == [
                first,
                second,
            ]
            assert state.claim_state(owner.id) == 'claimed'
            assert not state.claim_attempt(claim(parent, 'claim-b'))

        def test_refuses_a_conflicting_duplicate_without_overwriting_history(
            self, state: State
        ) -> None:
            parent, owner = claimed(state)
            original = entry(parent, owner)
            assert state.begin_attempt(start(owner))
            state.record_entry(owner.id, original.model_dump_json())
            changed = original.model_copy(
                update={'responses': [MeasuredUsage(120, 20, 0, 5, None)]}
            )

            with pytest.raises(InputError):
                state.record_entry(owner.id, changed.model_dump_json())

            assert state.entries('sample') == [original.model_dump_json()]
            assert state.claim_state(owner.id) == 'claimed'

        def test_other_sql_writers_cannot_reprice_a_recorded_attempt(
            self, state: State, tmp_path: Path
        ) -> None:
            parent, owner = claimed(state)
            original = entry(parent, owner)
            assert state.begin_attempt(start(owner))
            state.record_entry(owner.id, original.model_dump_json())

            with (
                closing(
                    sqlite3.connect(tmp_path / 'state.sqlite', autocommit=True)
                ) as other,
                pytest.raises(sqlite3.IntegrityError),
            ):
                other.execute(
                    'UPDATE attempt_entries SET entry_json = '
                    "json_set(entry_json, '$.price.input', 900)"
                )

            assert state.entries('sample') == [original.model_dump_json()]

    class TestSettleClaim:
        def test_requires_a_real_attempt_before_publishing_completion(
            self, state: State
        ) -> None:
            parent, owner = claimed(state)
            register(state, parent, owner)

            with pytest.raises(InputError):
                state.settle_claim(owner.id)

            assert state.claim_state(owner.id) == 'claimed'
            assert state.source_revision(parent.scope) == 0
            assert state.trial_artifact(parent.id, owner.trial)['commit_sha'] == COMMIT
            assert not state.complete_run(parent.id)

        def test_persists_artifacts_and_releases_a_slot_exactly_once(
            self, state: State
        ) -> None:
            parent, owner = claimed(state)
            settled(state, parent, owner)

            state.settle_claim(owner.id)

            assert state.claim_state(owner.id) == 'settled'
            assert state.source_revision(parent.scope) == 1
            (reference,) = state.artifacts(parent.scope)
            assert reference['commit_sha'] == COMMIT
            assert reference['path'] == 'runs/run-a/claim-a'
            assert reference['manifest_digest'] == DIGEST
            assert state.complete_run(parent.id)
            assert state.run_status(parent.id) == 'complete'

        def test_rejects_replacing_an_already_settled_artifact(
            self, state: State
        ) -> None:
            parent, owner = claimed(state)
            settled(state, parent, owner)
            before = state.artifacts(parent.scope)

            with pytest.raises(InputError):
                state.record_trial_artifact(
                    parent.id,
                    owner.trial,
                    owner.trial,
                    'f' * 40,
                    'runs/other',
                    'c' * 64,
                )

            assert state.artifacts(parent.scope) == before
            assert state.source_revision(parent.scope) == 1

        def test_failed_source_advance_rolls_back_the_artifact_and_claim_together(
            self, state: State, tmp_path: Path
        ) -> None:
            parent, owner = claimed(state)
            original = entry(parent, owner)
            assert state.begin_attempt(start(owner))
            state.record_entry(owner.id, original.model_dump_json())
            register(state, parent, owner)
            before = state.artifacts(parent.scope)
            with closing(
                sqlite3.connect(tmp_path / 'state.sqlite', autocommit=True)
            ) as other:
                other.executescript(
                    'CREATE TRIGGER refuse_source_advance '
                    'BEFORE UPDATE ON source_heads '
                    "BEGIN SELECT RAISE(ABORT, 'Synthetic storage failure'); END;"
                )

            with pytest.raises(
                sqlite3.IntegrityError, match='Synthetic storage failure'
            ):
                state.settle_claim(owner.id)

            assert state.claim_state(owner.id) == 'claimed'
            assert state.artifacts(parent.scope) == before
            assert state.source_revision(parent.scope) == 0
            assert state.entries(parent.scope) == [original.model_dump_json()]
            assert not state.complete_run(parent.id)

        def test_out_of_order_slots_advance_a_counter_and_refuse_stale_plans(
            self, state: State
        ) -> None:
            parent, first = claimed(state)
            second = claim(parent, 'claim-b', slot='task-v1/model-a/sample-1')
            assert state.claim_attempt(second)
            settled(state, parent, second)
            assert not state.complete_run(parent.id)

            settled(state, parent, first)

            assert state.source_revision(parent.scope) == 2
            assert (
                state.claim_state(first.id) == state.claim_state(second.id) == 'settled'
            )
            assert state.complete_run(parent.id)
            following = run('run-next')
            state.create_run(following)
            assert not state.claim_attempt(claim(following, 'stale'))
            fresh = claim(following, 'fresh', revision=2)
            assert state.claim_attempt(fresh)
            assert state.get_claim(fresh.id) == fresh

    class TestStageOutput:
        def test_retries_do_not_replace_or_duplicate_the_original_output(
            self, state: State
        ) -> None:
            original = output('first')
            state.begin_collection(collection('next', [original]))
            state.stage_output('next', 0, original)
            state.stage_output('next', 0, json.dumps(json.loads(original)))
            changed = json.loads(original)
            changed['text'] = 'Changed answer.'

            with pytest.raises(InputError):
                state.stage_output('next', 0, json.dumps(changed))

            state.seal_collection('next')
            assert state.activate_collection('next', None)
            assert state.outputs('sample') == [original]

    class TestSealCollection:
        def test_staging_is_invisible_until_a_complete_generation_is_activated(
            self, state: State
        ) -> None:
            old = output('old')
            sealed(state, 'old', old)
            assert state.activate_collection('old', None)
            first, second = output('first'), output('second')
            state.begin_collection(collection('next', [first, second]))
            state.stage_output('next', 1, second)
            assert state.outputs('sample') == [old]
            state.stage_output('next', 0, first)

            state.seal_collection('next')
            assert state.activate_collection('next', 'old')

            assert state.outputs('sample') == [first, second]

        @pytest.mark.parametrize('failure', ['missing', 'position', 'digest'])
        def test_refuses_incomplete_or_mismatched_output_generations(
            self, state: State, failure: str
        ) -> None:
            first, second = output('first'), output('second')
            pending = collection('next', [first, second])
            if failure == 'digest':
                pending = replace(pending, manifest_digest='0' * 64)
            state.begin_collection(pending)
            state.stage_output('next', 0, first)
            if failure != 'missing':
                state.stage_output('next', 2 if failure == 'position' else 1, second)

            with pytest.raises(InputError):
                state.seal_collection('next')

            assert state.outputs('sample') == []
            with pytest.raises(InputError):
                state.activate_collection('next', None)

        @pytest.mark.parametrize(
            'statement',
            [
                'UPDATE collection_outputs SET output_json = '
                "replace(output_json, 'Answer for first.', 'Changed answer.') "
                "WHERE collection_id = 'sealed'",
                "DELETE FROM collection_outputs WHERE collection_id = 'sealed'",
                'INSERT INTO collection_outputs '
                '(collection_id, position, output_key, output_json) '
                "SELECT collection_id, 1, 'another', output_json "
                "FROM collection_outputs WHERE collection_id = 'sealed'",
            ],
            ids=['update', 'delete', 'insert'],
        )
        def test_sealed_rows_remain_immutable_for_other_sql_writers(
            self, state: State, tmp_path: Path, statement: str
        ) -> None:
            original = output('first')
            sealed(state, 'sealed', original)
            assert state.activate_collection('sealed', None)

            with (
                closing(
                    sqlite3.connect(tmp_path / 'state.sqlite', autocommit=True)
                ) as other,
                pytest.raises(sqlite3.IntegrityError),
            ):
                other.execute(statement)

            assert state.outputs('sample') == [original]

        def test_an_empty_sealed_manifest_cannot_be_deleted_without_child_rows(
            self, state: State, tmp_path: Path
        ) -> None:
            sealed(state, 'empty')

            with (
                closing(
                    sqlite3.connect(tmp_path / 'state.sqlite', autocommit=True)
                ) as other,
                pytest.raises(sqlite3.IntegrityError),
            ):
                other.execute("DELETE FROM collections WHERE id = 'empty'")

            assert state.activate_collection('empty', None)
            assert state.outputs('sample') == []

    class TestActivateCollection:
        def test_two_connections_cannot_overwrite_the_same_observed_generation(
            self, state: State, tmp_path: Path
        ) -> None:
            sealed(state, 'first', output('first'))
            sealed(state, 'second', output('second'))
            barrier = Barrier(2)

            def activate(identifier: str) -> bool:
                with connect(tmp_path / 'state.sqlite') as writer:
                    barrier.wait()
                    return writer.activate_collection(identifier, None)

            with ThreadPoolExecutor(max_workers=2) as workers:
                results = list(workers.map(activate, ['first', 'second']))

            assert sorted(results) == [False, True]
            winner = 'first' if results[0] else 'second'
            assert state.outputs('sample') == [output(winner)]

        def test_a_stale_writer_cannot_replace_a_newer_collection(
            self, state: State
        ) -> None:
            for identifier in ['old', 'next', 'stale']:
                sealed(state, identifier, output(identifier))
            assert state.activate_collection('old', None)
            assert state.activate_collection('next', 'old')

            accepted = state.activate_collection('stale', 'old')

            assert not accepted
            assert state.outputs('sample') == [output('next')]

    class TestResetBaseline:
        def test_only_clears_the_pointer_and_keeps_canary_and_spend_history(
            self, state: State
        ) -> None:
            parent = run(scope='canaries')
            owner = claim(parent)
            state.create_run(parent)
            assert state.claim_attempt(owner)
            measured = entry(parent, owner)
            assert state.begin_attempt(start(owner))
            state.record_entry(owner.id, measured.model_dump_json())
            success = canary()
            failure = canary(job='run-b', error='Connection failed.')
            state.append_canary(parent.id, success.model_dump_json())
            state.append_canary(parent.id, json.dumps(success.model_dump(mode='json')))
            with pytest.raises(InputError):
                state.append_canary(
                    parent.id, canary(error='Different outcome.').model_dump_json()
                )
            other = run('run-b', scope='canaries')
            state.create_run(other)
            state.append_canary(other.id, failure.model_dump_json())
            assert state.establish_baseline('model-a', parent.id)
            assert not state.establish_baseline('model-a', other.id)
            assert state.baseline('model-a') == success.model_dump_json()

            state.reset_baseline('model-a')

            assert state.baseline('model-a') is None
            assert [
                Record.model_validate_json(item) for item in state.records('model-a')
            ] == [
                success,
                failure,
            ]
            assert state.entries('canaries') == [measured.model_dump_json()]
            assert state.claim_state(owner.id) == 'claimed'
