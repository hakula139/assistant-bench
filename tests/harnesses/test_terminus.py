import asyncio
from collections.abc import Callable, Iterator
from importlib import metadata
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from unittest.mock import MagicMock

import openai
import pytest
from harbor.agents.terminus_2.terminus_2 import Terminus2
from harbor.agents.terminus_2.tmux_session import TmuxSession
from harbor.environments.base import BaseEnvironment
from harbor.llms.base import (
    ContextLengthExceededError,
    LLMResponse,
    OutputLengthExceededError,
)
from harbor.llms.chat import Chat
from harbor.models.agent.context import AgentContext
from harbor.models.task import config as task_config
from harbor.models.trial.paths import TrialPaths
from openai.types.responses import Response

from assistant_bench.catalog import EXPORT_VAR, NixosConfigExport
from assistant_bench.generation import generation_version
from assistant_bench.harnesses import options
from assistant_bench.harnesses.endpoint import CA_FILE_VAR, LOCAL_ADDRESS_VAR
from assistant_bench.harnesses.host import HostEnvironment
from assistant_bench.harnesses.responses import ResponsesChat, ResponsesLLM
from assistant_bench.harnesses.terminus import Terminus
from support import (
    StubEndpoint,
    make_system,
    reasoned_response,
    response,
    terminus_completion,
)


pytestmark = pytest.mark.usefixtures('export')

OPTIONS = {'system': 's', 'fingerprint': 'f'}


@pytest.fixture
def terminal_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TmuxSession:
    environment = HostEnvironment(
        environment_dir=tmp_path / 'environment',
        environment_name='summary',
        session_id='summary__env',
        trial_paths=TrialPaths(tmp_path / 'trial'),
        task_env_config=task_config.EnvironmentConfig(),
    )
    session = TmuxSession(
        session_name='summary',
        environment=environment,
        logging_path=tmp_path / 'terminal.log',
        local_asciinema_recording_path=None,
        remote_asciinema_recording_path=None,
    )

    async def capture(self: TmuxSession, capture_entire: bool = False) -> str:
        return 'Terminal output: hello'

    async def alive(self: TmuxSession) -> bool:
        return True

    async def incremental(self: TmuxSession) -> str:
        return 'Terminal output: hello'

    monkeypatch.setattr(TmuxSession, 'capture_pane', capture)
    monkeypatch.setattr(TmuxSession, 'is_session_alive', alive)
    monkeypatch.setattr(TmuxSession, 'get_incremental_output', incremental)
    return session


@runtime_checkable
class NativeClient(Protocol):
    def get_model_context_limit(self) -> int: ...
    def get_model_output_limit(self) -> int | None: ...
    async def call(self, prompt: str, **kwargs: object) -> LLMResponse: ...


@runtime_checkable
class NativeConversation(Protocol):
    @property
    def total_input_tokens(self) -> int: ...
    @property
    def total_output_tokens(self) -> int: ...
    @property
    def total_cache_tokens(self) -> int: ...
    @property
    def messages(self) -> list[dict[str, Any]]: ...
    async def chat(self, prompt: str, **kwargs: object) -> LLMResponse: ...
    def reset_response_chain(self) -> None: ...


class NativeProbe(Terminus):
    async def execute(self, session: TmuxSession, context: AgentContext) -> None:
        self._session = session
        await self.run('Greet me.', session.environment, context)

    def closure_check(self) -> Callable[[], bool]:
        return self._endpoint_client.is_closed

    async def close(self) -> None:
        await self._endpoint_client.close()

    @property
    def client(self) -> NativeClient:
        assert isinstance(self._llm, NativeClient)
        return self._llm

    async def request(self, prompt: str) -> LLMResponse:
        try:
            return await self.client.call(prompt, **self._llm_call_kwargs)
        finally:
            await self.close()

    def conversation(self) -> NativeConversation:
        self._reset_per_run_state()
        chat = (
            ResponsesChat(self._llm)
            if isinstance(self._llm, ResponsesLLM)
            else Chat(self._llm)
        )
        assert isinstance(chat, NativeConversation)
        return chat

    async def follow(self, chat: NativeConversation, prompt: str) -> LLMResponse:
        return await chat.chat(prompt, **self._llm_call_kwargs)

    async def summarize(self, chat: NativeConversation, session: TmuxSession) -> str:
        assert isinstance(chat, Chat)
        handoff, _ = await self._summarize(chat, 'Greet me.', session)
        return handoff


@pytest.fixture
def responses_agent(endpoint: StubEndpoint, tmp_path: Path) -> Iterator[NativeProbe]:
    system = make_system(
        harness='terminus-2',
        api='openai-responses',
        effort='max',
        max_output_tokens=32768,
        context_window=131072,
    )
    agent = NativeProbe(
        logs_dir=tmp_path,
        model_name=f'openai/{system.route}',
        extra_env=endpoint.env,
        **options.terminus(system),
    )
    yield agent
    asyncio.run(agent.close())


class TestTerminus:
    @pytest.mark.parametrize(
        ('change', 'message'),
        [
            ({'llm_backend': 'tinker'}, 'requires the LiteLLM backend'),
            ({'model_info': None}, 'requires resolved model capacities'),
        ],
        ids=['unsupported-backend', 'missing-capacities'],
    )
    def test_rejects_an_incompatible_native_configuration(
        self,
        endpoint: StubEndpoint,
        tmp_path: Path,
        change: dict[str, Any],
        message: str,
    ) -> None:
        kwargs = options.terminus(make_system(harness='terminus-2')) | change

        with pytest.raises(ValueError, match=message):
            NativeProbe(
                logs_dir=tmp_path,
                model_name='openai/acme/large',
                extra_env=endpoint.env,
                **kwargs,
            )

        assert endpoint.requests == []

    def test_keeps_each_systems_context_reserve_when_routes_are_shared(
        self, endpoint: StubEndpoint, tmp_path: Path
    ) -> None:
        first = make_system(
            route='acme/shared-capacity',
            harness='terminus-2',
            max_output_tokens=64,
            context_window=8192,
        )
        second = first.model_copy(
            update={'name': 'other', 'max_output_tokens': 128, 'context_window': 16384}
        )
        first_agent = NativeProbe(
            logs_dir=tmp_path / 'first',
            model_name=f'openai/{first.route}',
            extra_env=endpoint.env,
            **options.terminus(first),
        )

        second_agent = NativeProbe(
            logs_dir=tmp_path / 'second',
            model_name=f'openai/{second.route}',
            extra_env=endpoint.env,
            **options.terminus(second),
        )

        assert first_agent.client.get_model_context_limit() == 8128
        assert first_agent.client.get_model_output_limit() == 64
        assert second_agent.client.get_model_context_limit() == 16256
        assert second_agent.client.get_model_output_limit() == 128
        assert endpoint.requests == []

    def test_uses_native_context_management_without_an_artificial_turn_limit(
        self, endpoint: StubEndpoint, tmp_path: Path
    ) -> None:
        system = make_system(
            route='acme/context-check',
            harness='terminus-2',
            max_output_tokens=32768,
            context_window=131072,
        )
        agent = NativeProbe(
            logs_dir=tmp_path,
            model_name=f'openai/{system.route}',
            extra_env=endpoint.env,
            **options.terminus(system),
        )

        assert agent.client.get_model_context_limit() == 98304
        assert agent.client.get_model_output_limit() == 32768
        assert agent.options.max_turns is None
        assert agent.version() == generation_version('terminus-2')
        assert endpoint.requests == []

    class TestPreflight:
        def test_rejects_an_effort_the_agent_does_not_accept(
            self, endpoint: StubEndpoint
        ) -> None:
            with pytest.raises(ValueError, match='reasoning_effort'):
                Terminus.preflight(
                    OPTIONS | {'reasoning_effort': 'turbo'}, endpoint.env
                )

        def test_checks_the_endpoint_variables(
            self, monkeypatch: pytest.MonkeyPatch
        ) -> None:
            monkeypatch.delenv(EXPORT_VAR)

            with pytest.raises(ValueError, match='^Set `BENCH_API_BASE_URL`'):
                Terminus.preflight(OPTIONS, {})

    class TestSummarize:
        @pytest.mark.parametrize('empty', [False, True], ids=['text', 'reasoning-only'])
        def test_keeps_each_subagent_and_the_handoff_on_its_own_native_items(
            self,
            endpoint: StubEndpoint,
            responses_agent: NativeProbe,
            terminal_session: TmuxSession,
            empty: bool,
        ) -> None:
            main = reasoned_response('Started.', 'main')
            summary = reasoned_response(None if empty else 'Summary.', 'summary')
            questions = reasoned_response(None if empty else 'Questions.', 'questions')
            answers = reasoned_response('Answers.', 'answers')
            endpoint.responses = [
                main,
                summary,
                questions,
                answers,
                reasoned_response('Continued.', 'continued'),
            ]
            for payload in endpoint.responses:
                Response.model_validate(payload)
            chat = responses_agent.conversation()

            async def summarize() -> str:
                await responses_agent.follow(chat, 'Read the greeting.')
                handoff = await responses_agent.summarize(chat, terminal_session)
                await responses_agent.follow(chat, handoff)
                return handoff

            handoff = asyncio.run(summarize())

            _, summarized, questioned, answered, continued = endpoint.requests
            assert summarized.body['input'][:-1] == [
                {'role': 'user', 'content': 'Read the greeting.'},
                *main['output'],
            ]
            (question_prompt,) = questioned.body['input']
            assert question_prompt['role'] == 'user'
            assert answered.body['input'] == [
                *summarized.body['input'],
                *summary['output'],
                answered.body['input'][-1],
            ]
            assert answered.body['input'][-1]['role'] == 'user'
            if not empty:
                assert 'Questions.' in answered.body['input'][-1]['content']
            assert continued.body['input'] == [
                {'role': 'user', 'content': 'Read the greeting.'},
                question_prompt,
                *questions['output'],
                {'role': 'user', 'content': handoff},
            ]
            assert 'Answers.' in handoff
            assert all(request.body['store'] is False for request in endpoint.requests)
            assert all(
                'previous_response_id' not in request.body
                for request in endpoint.requests
            )

        def test_discards_items_from_a_failed_summary_before_the_next_summary(
            self,
            endpoint: StubEndpoint,
            responses_agent: NativeProbe,
            terminal_session: TmuxSession,
        ) -> None:
            main = reasoned_response('Started.', 'main')
            stale = reasoned_response('Old summary.', 'stale-summary')
            failed = response('Failed questions.', status='failed')
            fresh = reasoned_response('Fresh summary.', 'fresh-summary')
            questions = reasoned_response('Fresh questions.', 'fresh-questions')
            endpoint.responses = [
                main,
                stale,
                failed,
                fresh,
                questions,
                reasoned_response('Fresh answers.', 'fresh-answers'),
                reasoned_response('Continued.', 'continued'),
            ]
            chat = responses_agent.conversation()

            async def summarize() -> str:
                await responses_agent.follow(chat, 'Read the greeting.')
                with pytest.raises(openai.APIError):
                    await responses_agent.summarize(chat, terminal_session)
                handoff = await responses_agent.summarize(chat, terminal_session)
                await responses_agent.follow(chat, handoff)
                return handoff

            handoff = asyncio.run(summarize())

            _, _, _, summarized, questioned, answered, continued = endpoint.requests
            assert summarized.body['input'][:-1] == [
                {'role': 'user', 'content': 'Read the greeting.'},
                *main['output'],
            ]
            assert answered.body['input'] == [
                *summarized.body['input'],
                *fresh['output'],
                answered.body['input'][-1],
            ]
            assert answered.body['input'][-1]['role'] == 'user'
            assert 'Fresh questions.' in answered.body['input'][-1]['content']
            assert continued.body['input'] == [
                {'role': 'user', 'content': 'Read the greeting.'},
                *questioned.body['input'],
                *questions['output'],
                {'role': 'user', 'content': handoff},
            ]

    class TestRun:
        def test_keeps_http_failure_usage_unknown_through_native_retries(
            self,
            endpoint: StubEndpoint,
            responses_agent: NativeProbe,
            terminal_session: TmuxSession,
        ) -> None:
            endpoint.status = 503
            endpoint.response = {'error': {'message': 'Unavailable.'}}
            context = AgentContext()

            with pytest.raises(openai.InternalServerError):
                asyncio.run(responses_agent.execute(terminal_session, context))

            assert len(endpoint.requests) == 3
            assert (
                context.n_input_tokens,
                context.n_cache_tokens,
                context.n_output_tokens,
                context.cost_usd,
            ) == (None, None, None, None)
            assert context.metadata is not None
            assert [
                call['response_status'] for call in context.metadata['responses']
            ] == [
                'request_failed',
                'request_failed',
                'request_failed',
            ]

        def test_maps_the_provider_context_error_to_native_context_management(
            self, endpoint: StubEndpoint, responses_agent: NativeProbe
        ) -> None:
            endpoint.status = 400
            endpoint.response = {
                'error': {
                    'code': 'context_length_exceeded',
                    'message': 'The input context is full.',
                    'type': 'invalid_request_error',
                }
            }

            with pytest.raises(ContextLengthExceededError):
                asyncio.run(responses_agent.request('Greet me.'))

            assert len(endpoint.requests) == 1

        def test_native_budget_reissue_preserves_partial_items_and_all_reported_usage(
            self,
            endpoint: StubEndpoint,
            responses_agent: NativeProbe,
            terminal_session: TmuxSession,
        ) -> None:
            partial = reasoned_response('{"analysis": "partial', 'partial')
            partial['status'] = 'incomplete'
            partial['incomplete_details'] = {'reason': 'max_output_tokens'}
            partial['output'][-1]['status'] = 'incomplete'
            text = terminus_completion()['choices'][0]['message']['content']
            endpoint.responses = [
                partial,
                reasoned_response(text, 'finished', 'final_answer'),
                reasoned_response(text, 'confirmed', 'final_answer'),
            ]
            context = AgentContext()

            asyncio.run(responses_agent.execute(terminal_session, context))

            first, reissued, confirmed = endpoint.requests
            assert reissued.body['input'][:-1] == [
                *first.body['input'],
                *partial['output'],
            ]
            assert reissued.body['input'][-1]['role'] == 'user'
            assert '32768 tokens' in reissued.body['input'][-1]['content']
            assert (
                confirmed.body['input'][: len(reissued.body['input'])]
                == reissued.body['input']
            )
            assert (
                context.n_input_tokens,
                context.n_cache_tokens,
                context.n_output_tokens,
            ) == (360, 300, 120)
            assert context.metadata is not None
            assert [
                call['finish_reason'] for call in context.metadata['responses']
            ] == [
                'length',
                'stop',
                'stop',
            ]

        @pytest.mark.parametrize('unknown', [0, 1], ids=['first', 'last'])
        def test_keeps_aggregate_usage_unknown_if_any_native_response_omits_it(
            self,
            endpoint: StubEndpoint,
            responses_agent: NativeProbe,
            terminal_session: TmuxSession,
            unknown: int,
        ) -> None:
            text = terminus_completion()['choices'][0]['message']['content']
            endpoint.responses = [
                reasoned_response(text, 'first'),
                reasoned_response(text, 'last', 'final_answer'),
            ]
            endpoint.responses[unknown].pop('usage')
            context = AgentContext()

            asyncio.run(responses_agent.execute(terminal_session, context))

            assert len(endpoint.requests) == 2
            assert (
                context.n_input_tokens,
                context.n_cache_tokens,
                context.n_output_tokens,
                context.cost_usd,
            ) == (None, None, None, None)
            assert context.metadata is not None
            calls = context.metadata['responses']
            assert 'input_tokens' not in calls[unknown]
            assert calls[1 - unknown]['input_tokens'] == 120
            assert calls[1 - unknown]['cache_tokens'] == 100
            assert calls[1 - unknown]['output_tokens'] == 40

        @pytest.mark.parametrize(
            ('status', 'reason', 'refusal', 'error'),
            [
                ('failed', None, None, True),
                ('queued', None, None, False),
                ('cancelled', None, None, False),
                ('incomplete', 'content_filter', None, False),
                ('incomplete', None, None, False),
                ('completed', None, 'I cannot answer.', False),
                ('completed', None, None, True),
            ],
            ids=[
                'failed',
                'queued',
                'cancelled',
                'filtered',
                'unknown-incomplete',
                'refused',
                'error-with-completed-status',
            ],
        )
        def test_rejects_unusable_native_responses_without_retrying(
            self,
            endpoint: StubEndpoint,
            tmp_path: Path,
            status: str,
            reason: str | None,
            refusal: str | None,
            error: bool,
            terminal_session: TmuxSession,
        ) -> None:
            payload = response(
                'Partial answer.',
                status=status,
                incomplete_reason=reason,
                refusal=refusal,
            )
            if error:
                payload['error'] = {'code': 'server_error', 'message': 'Unavailable.'}
            Response.model_validate(payload)
            endpoint.response = payload
            system = make_system(
                harness='terminus-2',
                api='openai-responses',
                max_output_tokens=32768,
                context_window=131072,
            )
            agent = NativeProbe(
                logs_dir=tmp_path,
                model_name=f'openai/{system.route}',
                extra_env=endpoint.env,
                **options.terminus(system),
            )
            context = AgentContext()

            with pytest.raises(openai.APIError):
                asyncio.run(agent.execute(terminal_session, context))

            assert len(endpoint.requests) == 1
            assert (
                context.n_input_tokens,
                context.n_cache_tokens,
                context.n_output_tokens,
            ) == (120, 100, 40)
            assert context.cost_usd is None
            assert context.metadata is not None
            (recorded,) = context.metadata['responses']
            assert recorded['response_status'] == status
            assert recorded['request_id'] == 'req-1'
            assert recorded['response_model'] == 'large-2026'
            assert recorded['reasoning_tokens'] == 25
            assert recorded['incomplete_reason'] == reason
            assert recorded['refusals'] == ([refusal] if refusal else [])
            assert recorded['response_error'] == payload['error']
            assert recorded['latency_sec'] >= 0
            assert Response.model_validate_json(
                (tmp_path / 'response-1.json').read_text()
            ) == Response.model_validate(payload)

        def test_preserves_native_budget_exhaustion_and_its_exact_partial_text(
            self, endpoint: StubEndpoint, tmp_path: Path
        ) -> None:
            endpoint.response = response(
                ' leading partial \n',
                status='incomplete',
                incomplete_reason='max_output_tokens',
            )
            system = make_system(harness='terminus-2', api='openai-responses')
            agent = NativeProbe(
                logs_dir=tmp_path,
                model_name=f'openai/{system.route}',
                extra_env=endpoint.env,
                **options.terminus(system),
            )

            with pytest.raises(OutputLengthExceededError) as error:
                asyncio.run(agent.request('Greet me.'))

            assert error.value.truncated_response == ' leading partial \n'
            assert len(endpoint.requests) == 1

        def test_responses_keeps_native_tool_history_without_server_state(
            self, endpoint: StubEndpoint, tmp_path: Path
        ) -> None:
            system = make_system(
                route='acme/native-responses',
                api='openai-responses',
                harness='terminus-2',
                effort='max',
                max_output_tokens=32768,
                context_window=131072,
            )
            command = terminus_completion('cat greeting.txt\n', task_complete=False)
            tool_text = command['choices'][0]['message']['content']
            final_text = terminus_completion()['choices'][0]['message']['content']
            tool_response = reasoned_response(tool_text, 'resp-tool')
            final_response = reasoned_response(final_text, 'resp-final', 'final_answer')
            for payload in (tool_response, final_response):
                Response.model_validate(payload)
            endpoint.responses = [tool_response, final_response]
            endpoint.response = response('Resumed.', response_id='resp-resumed')
            agent = NativeProbe(
                logs_dir=tmp_path,
                model_name=f'openai/{system.route}',
                extra_env=endpoint.env,
                **options.terminus(system),
            )
            chat = agent.conversation()

            async def converse() -> tuple[
                LLMResponse, LLMResponse, LLMResponse, LLMResponse
            ]:
                try:
                    first = await agent.follow(chat, 'Read the greeting.')
                    second = await agent.follow(chat, 'Terminal output: hello')
                    chat.reset_response_chain()
                    third = await agent.follow(
                        chat, 'Continue using the earlier result.'
                    )
                    chat.messages[:] = [
                        {'role': 'user', 'content': 'Summary: the greeting is hello.'}
                    ]
                    chat.reset_response_chain()
                    fourth = await agent.follow(chat, 'Continue from the summary.')
                    return first, second, third, fourth
                finally:
                    await agent.close()

            first, second, third, fourth = asyncio.run(converse())

            assert (first.content, second.content, third.content, fourth.content) == (
                tool_text,
                final_text,
                'Resumed.',
                'Resumed.',
            )
            initial, followup, resumed, summarized = endpoint.requests
            for request in endpoint.requests:
                assert request.path == '/v1/responses'
                assert request.body['reasoning'] == {'effort': 'max'}
                assert request.body['max_output_tokens'] == 32768
                assert request.body['store'] is False
                assert 'previous_response_id' not in request.body
                assert 'max_completion_tokens' not in request.body
                assert request.headers['authorization'] == 'Bearer test-key'
                assert request.headers['x-trace'] == 'bench'
            assert initial.body['input'] == [
                {'role': 'user', 'content': 'Read the greeting.'}
            ]
            assert followup.body['input'] == [
                {'role': 'user', 'content': 'Read the greeting.'},
                *tool_response['output'],
                {'role': 'user', 'content': 'Terminal output: hello'},
            ]
            assert 'previous_response_id' not in resumed.body
            assert resumed.body['input'] == [
                {'role': 'user', 'content': 'Read the greeting.'},
                *tool_response['output'],
                {'role': 'user', 'content': 'Terminal output: hello'},
                *final_response['output'],
                {'role': 'user', 'content': 'Continue using the earlier result.'},
            ]
            assert summarized.body['input'] == [
                {'role': 'user', 'content': 'Summary: the greeting is hello.'},
                {'role': 'user', 'content': 'Continue from the summary.'},
            ]
            assert (
                chat.total_input_tokens,
                chat.total_output_tokens,
                chat.total_cache_tokens,
            ) == (480, 160, 400)
            assert agent.client.get_model_context_limit() == 98304
            assert agent.client.get_model_output_limit() == 32768

        @pytest.mark.parametrize('effort', ['high', 'max'])
        @pytest.mark.parametrize('api', ['openai-chat-completions', 'openai-responses'])
        def test_forwards_the_configured_quality_settings_through_the_native_client(
            self, endpoint: StubEndpoint, tmp_path: Path, effort: str, api: str
        ) -> None:
            if api == 'openai-responses':
                endpoint.response = response()
            system = make_system(
                route='acme/native-quality',
                api=api,
                harness='terminus-2',
                effort=effort,
                max_output_tokens=32768,
                context_window=131072,
            )
            agent = NativeProbe(
                logs_dir=tmp_path,
                model_name=f'openai/{system.route}',
                extra_env=endpoint.env,
                temperature=0.7,
                **options.terminus(system),
            )

            result = asyncio.run(agent.request('Greet me.'))

            (request,) = endpoint.requests
            assert request.body['model'] == system.route
            assert request.body['temperature'] == 0.7
            if api == 'openai-responses':
                assert request.path == '/v1/responses'
                assert request.body['reasoning'] == {'effort': effort}
                assert request.body['max_output_tokens'] == 32768
            else:
                assert request.path == '/v1/chat/completions'
                assert request.body['reasoning_effort'] == effort
                assert request.body['max_completion_tokens'] == 32768
            assert request.headers['authorization'] == 'Bearer test-key'
            assert request.headers['x-trace'] == 'bench'
            assert result.content == 'Hello there.'
            assert result.usage is not None
            assert result.usage.prompt_tokens == 120
            assert result.usage.completion_tokens == 40
            assert result.usage.cache_tokens == 100

        @pytest.mark.parametrize('api', ['openai-chat-completions', 'openai-responses'])
        def test_uses_the_configured_ca_and_local_address_for_native_calls(
            self, tls_endpoint: StubEndpoint, tmp_path: Path, api: str
        ) -> None:
            assert tls_endpoint.ca_file is not None
            if api == 'openai-responses':
                tls_endpoint.response = response()
            system = make_system(harness='terminus-2', api=api)
            agent = NativeProbe(
                logs_dir=tmp_path,
                model_name=f'openai/{system.route}',
                extra_env=tls_endpoint.env
                | {
                    CA_FILE_VAR: str(tls_endpoint.ca_file),
                    LOCAL_ADDRESS_VAR: '127.0.0.1',
                },
                **options.terminus(system),
            )
            is_closed = agent.closure_check()

            result = asyncio.run(agent.request('Greet me.'))

            assert result.content == 'Hello there.'
            (sent,) = tls_endpoint.requests
            assert sent.path == (
                '/v1/responses' if api == 'openai-responses' else '/v1/chat/completions'
            )
            assert sent.headers['authorization'] == 'Bearer test-key'
            assert sent.headers['x-trace'] == 'bench'
            assert is_closed()

        @pytest.mark.parametrize('api', ['openai-chat-completions', 'openai-responses'])
        @pytest.mark.parametrize('fails', [False, True], ids=['success', 'failure'])
        def test_closes_the_transport_and_keeps_metadata_after_the_parent_finishes(
            self,
            agent_export: tuple[dict[str, str], NixosConfigExport],
            endpoint: StubEndpoint,
            monkeypatch: pytest.MonkeyPatch,
            tmp_path: Path,
            api: str,
            fails: bool,
        ) -> None:
            env, export = agent_export
            kwargs = options.terminus(make_system(harness='terminus-2', api=api))
            Terminus.preflight(kwargs, endpoint.env | env)
            agent = NativeProbe(
                logs_dir=tmp_path,
                model_name='openai/acme/large',
                extra_env=endpoint.env | env,
                **kwargs,
            )
            context = AgentContext()
            is_closed = agent.closure_check()

            async def finish(
                self: Terminus2,
                instruction: str,
                environment: BaseEnvironment,
                context: AgentContext,
            ) -> None:
                context.metadata = {'n_episodes': 1}
                if fails:
                    raise RuntimeError('agent failed')

            monkeypatch.setattr(Terminus2, 'run', finish)

            if fails:
                with pytest.raises(RuntimeError, match='agent failed'):
                    asyncio.run(
                        agent.run('Go.', MagicMock(spec=BaseEnvironment), context)
                    )
            else:
                asyncio.run(agent.run('Go.', MagicMock(spec=BaseEnvironment), context))

            assert is_closed()
            assert context.metadata == {
                'n_episodes': 1,
                'nixos_config_rev': export.rev,
                'harbor_version': metadata.version('harbor'),
                **({'responses': []} if api == 'openai-responses' else {}),
            }
