import asyncio
from datetime import timedelta

import pytest

from assistant_bench.harnesses.accounting import (
    AccountingError,
    RequestReceipt,
    TrialAccounting,
    current_trial,
)
from assistant_bench.harnesses.endpoint import Endpoint
from assistant_bench.harnesses.usage import MeasuredUsage
from assistant_bench.state import RequestAdmission, State
from support import START, StubEndpoint, accounted_trial


USAGE = MeasuredUsage(100, 20, 0, 5, None)


def receipt(*, usage: MeasuredUsage | None = USAGE) -> RequestReceipt:
    return RequestReceipt(
        api='openai-responses', outcome='completed', http_status=200, usage=usage
    )


class TestTrialAccounting:
    class TestBegin:
        def test_admission_requires_a_confirmed_attempt_start(
            self, private_state: State
        ) -> None:
            with accounted_trial(private_state) as recorder:
                with pytest.raises(AccountingError, match='cannot admit'):
                    asyncio.run(recorder.admit('openai-responses'))

                assert recorder.fatal
                assert private_state.requests('sample') == []
                assert private_state.claim_state(recorder.claim_id) == 'uncertain'

        def test_cannot_start_the_same_paid_attempt_twice(
            self, bound_trial: TrialAccounting, private_state: State
        ) -> None:
            bound_trial.begin(START)

            with pytest.raises(AccountingError, match='cannot start again'):
                bound_trial.begin(START + timedelta(seconds=1))

            assert bound_trial.fatal
            assert private_state.claim_state(bound_trial.claim_id) == 'uncertain'

    class TestAdmit:
        def test_refused_admission_cannot_grant_paid_io(
            self,
            begun_trial: TrialAccounting,
            private_state: State,
            monkeypatch: pytest.MonkeyPatch,
        ) -> None:
            def refuse(self: State, request: RequestAdmission) -> bool:
                return False

            monkeypatch.setattr(State, 'admit_request', refuse)

            with pytest.raises(AccountingError, match='was not granted'):
                asyncio.run(begun_trial.admit('openai-responses'))

            assert begun_trial.fatal
            assert private_state.requests('sample') == []
            assert private_state.claim_state(begun_trial.claim_id) == 'uncertain'

    class TestConfirm:
        def test_keeps_request_order_and_unknown_usage_after_out_of_order_receipts(
            self, begun_trial: TrialAccounting, private_state: State
        ) -> None:
            async def complete() -> None:
                first = await begun_trial.admit('openai-responses')
                second = await begun_trial.admit('openai-responses')
                await begun_trial.confirm(second, receipt())
                await begun_trial.confirm(first, receipt(usage=None))
                await begun_trial.confirm(second, receipt())

            asyncio.run(complete())
            begun_trial.finish(START + timedelta(seconds=1))

            attempt = begun_trial.attempt()
            assert attempt is not None
            assert attempt.responses == [None, USAGE]
            assert attempt.started_at == START
            assert not begun_trial.fatal
            assert all(
                row['receipt_json'] is not None
                for row in private_state.requests('sample')
            )

        def test_failed_receipt_persistence_latches_the_attempt_even_after_catch(
            self,
            begun_trial: TrialAccounting,
            private_state: State,
            monkeypatch: pytest.MonkeyPatch,
        ) -> None:
            request_id = asyncio.run(begun_trial.admit('openai-responses'))

            def unavailable(self: State, request_id: str, receipt_json: str) -> None:
                raise OSError('Database unavailable.')

            monkeypatch.setattr(State, 'record_receipt', unavailable)

            with pytest.raises(AccountingError, match='receipt could not'):
                asyncio.run(begun_trial.confirm(request_id, receipt()))
            with pytest.raises(AccountingError, match='cannot admit'):
                asyncio.run(begun_trial.admit('openai-responses'))
            begun_trial.finish(START + timedelta(seconds=1))

            (request,) = private_state.requests('sample')
            assert request['receipt_json'] is None
            assert private_state.claim_state(begun_trial.claim_id) == 'uncertain'
            attempt = begun_trial.attempt()
            assert attempt is not None
            assert attempt.responses == [None]

    class TestFinish:
        def test_an_interrupted_request_stays_unknown_and_cannot_restart(
            self, begun_trial: TrialAccounting, private_state: State
        ) -> None:
            asyncio.run(begun_trial.admit('openai-responses'))

            begun_trial.finish(START + timedelta(seconds=1))

            attempt = begun_trial.attempt()
            assert attempt is not None
            assert attempt.responses == [None]
            assert begun_trial.fatal
            assert private_state.claim_state(begun_trial.claim_id) == 'uncertain'
            with pytest.raises(AccountingError, match='cannot admit'):
                asyncio.run(begun_trial.admit('openai-responses'))

        def test_setup_failure_has_no_invented_attempt(
            self, bound_trial: TrialAccounting
        ) -> None:
            bound_trial.finish(START)

            assert bound_trial.attempt() is None


class TestCurrentTrial:
    def test_missing_context_refuses_paid_work_before_network_io(
        self, endpoint: StubEndpoint
    ) -> None:
        configured = Endpoint.read(endpoint.env.get, 'openai-responses')

        async def request() -> None:
            async with configured.http_client('openai-responses') as client:
                await client.post(f'{endpoint.url}/v1/responses', json={})

        with pytest.raises(AccountingError, match='no bound paid trial'):
            asyncio.run(request())

        assert endpoint.requests == []

    def test_binding_is_removed_when_the_trial_context_raises(
        self, private_state: State
    ) -> None:
        with (
            pytest.raises(RuntimeError, match='Trial failed'),
            accounted_trial(private_state) as recorder,
        ):
            assert current_trial() is recorder
            raise RuntimeError('Trial failed.')

        with pytest.raises(AccountingError, match='no bound paid trial'):
            current_trial()
