import asyncio
import json
import threading
from collections.abc import Callable, Iterator
from importlib import metadata
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from unittest.mock import MagicMock

import anthropic
import httpx
import openai
import pytest
from anthropic import AsyncAnthropic
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
from harbor.models.trajectories import SubagentTrajectoryRef
from harbor.models.trial.paths import TrialPaths
from litellm.exceptions import ServiceUnavailableError
from openai.types.chat import ChatCompletion
from openai.types.responses import Response

from assistant_bench import jobs
from assistant_bench.catalog import EXPORT_VAR, Api, NixosConfigExport, Price, System
from assistant_bench.families import load_family
from assistant_bench.generation import load_generation
from assistant_bench.harnesses import agent_kwargs
from assistant_bench.harnesses.endpoint import CA_FILE_VAR, LOCAL_ADDRESS_VAR
from assistant_bench.harnesses.host import HostEnvironment
from assistant_bench.harnesses.messages import (
    MessageCompletionError,
    MessagesChat,
    MessagesLLM,
    MessageStreamError,
)
from assistant_bench.harnesses.responses import (
    ResponseCompletionError,
    ResponsesChat,
    ResponsesLLM,
)
from assistant_bench.harnesses.terminus import Terminus
from assistant_bench.outputs import Output, StopDetails
from support import (
    StubEndpoint,
    completion,
    make_system,
    message,
    message_stream,
    reasoned_response,
    response,
    terminus_completion,
    write_task_family,
    write_trial,
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
    results: list[dict[str, Any]]

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

    async def call(self, prompt: str) -> LLMResponse:
        return await self.client.call(prompt, **self._llm_call_kwargs)

    async def request(self, prompt: str) -> LLMResponse:
        try:
            return await self.call(prompt)
        finally:
            await self.close()

    def conversation(self) -> NativeConversation:
        self._reset_per_run_state()
        chat = (
            MessagesChat(self._llm)
            if isinstance(self._llm, MessagesLLM)
            else ResponsesChat(self._llm)
            if isinstance(self._llm, ResponsesLLM)
            else Chat(self._llm)
        )
        assert isinstance(chat, NativeConversation)
        return chat

    async def follow(self, chat: NativeConversation, prompt: str) -> LLMResponse:
        return await chat.chat(prompt, **self._llm_call_kwargs)

    async def summarize(self, chat: NativeConversation, session: TmuxSession) -> str:
        assert isinstance(chat, Chat)
        handoff, _ = await self.summarize_result(chat, 'Greet me.', session)
        return handoff

    async def summarize_result(
        self, chat: Chat, instruction: str, session: TmuxSession
    ) -> tuple[str, list[SubagentTrajectoryRef] | None]:
        return await self._summarize(chat, instruction, session)


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
        **agent_kwargs(system),
    )
    yield agent
    asyncio.run(agent.close())


@pytest.fixture
def messages_agent(
    endpoint: StubEndpoint, tmp_path: Path, messages_clients: list[AsyncAnthropic]
) -> Iterator[NativeProbe]:
    system = make_system(
        harness='terminus-2',
        api='anthropic-messages',
        thinking_mode='anthropic-adaptive',
        effort='high',
        max_output_tokens=32768,
        context_window=131072,
    )
    agent = NativeProbe(
        logs_dir=tmp_path,
        model_name=f'openai/{system.route}',
        extra_env=endpoint.env,
        **agent_kwargs(system),
    )
    yield agent
    asyncio.run(agent.close())


def run_and_collect(
    system: System,
    endpoint: StubEndpoint,
    terminal_session: TmuxSession,
    tmp_path: Path,
    families_dir: Path,
    data_dir: Path,
    error: type[Exception] | None = None,
) -> tuple[AgentContext, Output]:
    """Run Terminus on a one-task family, then record and collect its trial.

    A run expected to fail with `error` records its type as Harbor does.
    """
    write_task_family(families_dir, 'echo', systems=(system.name,))
    family = load_family('f')
    config = jobs.job(family, [system], [])
    agent = NativeProbe(
        logs_dir=tmp_path,
        model_name=f'openai/{system.route}',
        extra_env=endpoint.env,
        **agent_kwargs(system),
    )
    context = AgentContext()

    if error is None:
        asyncio.run(agent.execute(terminal_session, context))
    else:
        with pytest.raises(error):
            asyncio.run(agent.execute(terminal_session, context))
    write_trial(
        data_dir / 'jobs' / family.name / 'job-1',
        'echo__1',
        config.agents[0],
        task=f'{family.name}/echo',
        version='1.0.0',
        instruction=None,
        context=context,
        error=error.__name__ if error else None,
        environment=config.environment,
        verifier_disabled=config.verifier.disable,
    )
    (collected,) = jobs.collect(family, {system.name: system})
    return context, collected


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
        kwargs = agent_kwargs(make_system(harness='terminus-2')) | change

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
            **agent_kwargs(first),
        )

        second_agent = NativeProbe(
            logs_dir=tmp_path / 'second',
            model_name=f'openai/{second.route}',
            extra_env=endpoint.env,
            **agent_kwargs(second),
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
            **agent_kwargs(system),
        )

        assert agent.client.get_model_context_limit() == 98304
        assert agent.client.get_model_output_limit() == 32768
        assert agent.options.max_turns is None
        assert agent.version() == load_generation().version('terminus-2')
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
        @pytest.mark.parametrize('empty', [False, True], ids=['text', 'thinking-only'])
        def test_native_blocks_follow_their_summary_branch_and_selected_context(
            self,
            endpoint: StubEndpoint,
            messages_agent: NativeProbe,
            terminal_session: TmuxSession,
            empty: bool,
        ) -> None:
            main = message('Started.', message_id='main')
            summary = message('' if empty else 'Summary.', message_id='summary')
            questions = message('' if empty else 'Questions.', message_id='questions')
            answers = message('Answers.', message_id='answers')
            continued = message('Continued.', message_id='continued')
            endpoint.streams = [
                message_stream(payload)
                for payload in (
                    main,
                    summary,
                    questions,
                    answers,
                    continued,
                    message('Selected.', message_id='selected'),
                )
            ]
            chat = messages_agent.conversation()

            async def summarize() -> str:
                await messages_agent.follow(chat, 'Read the greeting.')
                handoff = await messages_agent.summarize(chat, terminal_session)
                await messages_agent.follow(chat, handoff)
                chat.messages[:] = chat.messages[-2:]
                chat.reset_response_chain()
                await messages_agent.follow(chat, 'Continue selected context.')
                return handoff

            handoff = asyncio.run(summarize())

            _, summarized, questioned, answered, resumed, selected = endpoint.requests
            assert summarized.body['messages'][:-1] == [
                {'role': 'user', 'content': 'Read the greeting.'},
                {'role': 'assistant', 'content': main['content']},
            ]
            (question_prompt,) = questioned.body['messages']
            assert question_prompt['role'] == 'user'
            assert answered.body['messages'][:-1] == [
                *summarized.body['messages'],
                {'role': 'assistant', 'content': summary['content']},
            ]
            if not empty:
                assert 'Questions.' in answered.body['messages'][-1]['content']
            assert resumed.body['messages'] == [
                {'role': 'user', 'content': 'Read the greeting.'},
                question_prompt,
                {'role': 'assistant', 'content': questions['content']},
                {'role': 'user', 'content': handoff},
            ]
            assert selected.body['messages'] == [
                {'role': 'user', 'content': handoff},
                {'role': 'assistant', 'content': continued['content']},
                {'role': 'user', 'content': 'Continue selected context.'},
            ]
            assert 'Answers.' in handoff
            assert (
                chat.total_input_tokens,
                chat.total_cache_tokens,
                chat.total_output_tokens,
            ) == (390, 300, 120)
            assert isinstance(messages_agent.client, MessagesLLM)
            assert (
                sum(result['input_tokens'] for result in messages_agent.client.results)
                == 780
            )

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
        @pytest.mark.parametrize('api', ['openai-chat-completions', 'openai-responses'])
        def test_openai_cache_writes_survive_terminal_aggregation_and_collection(
            self,
            endpoint: StubEndpoint,
            terminal_session: TmuxSession,
            tmp_path: Path,
            families_dir: Path,
            data_dir: Path,
            api: Api,
        ) -> None:
            payload = terminus_completion()
            if api == 'openai-responses':
                payload = response(payload['choices'][0]['message']['content'])
            details = (
                'input_tokens_details'
                if api == 'openai-responses'
                else 'prompt_tokens_details'
            )
            payload['usage'][details]['cache_write_tokens'] = 10
            endpoint.responses = [payload, payload]
            system = make_system(
                harness='terminus-2',
                api=api,
                max_output_tokens=32768,
                context_window=131072,
                price=Price(input=2.0, output=10.0, cache_read=0.2),
            )

            context, collected = run_and_collect(
                system, endpoint, terminal_session, tmp_path, families_dir, data_dir
            )

            assert (
                collected.input_tokens,
                collected.cached_tokens,
                collected.output_tokens,
            ) == (240, 200, 80)
            assert collected.cache_write_tokens == 20
            assert collected.cost_usd is None
            assert collected.finish_reason is None
            assert context.metadata is not None
            assert [
                result['cache_write_tokens'] for result in context.metadata['responses']
            ] == [10, 10]
            assert len(endpoint.requests) == 2

        @pytest.mark.parametrize(
            ('reported', 'total'),
            [((25, 30), 55), ((0, 0), 0), ((25, None), None), ((None, 30), None)],
            ids=['reported', 'zero', 'missing-last', 'missing-first'],
        )
        def test_sums_reasoning_tokens_only_when_every_response_reports_them(
            self,
            endpoint: StubEndpoint,
            terminal_session: TmuxSession,
            tmp_path: Path,
            families_dir: Path,
            data_dir: Path,
            reported: tuple[int | None, ...],
            total: int | None,
        ) -> None:
            payloads: list[dict[str, Any]] = []
            for count in reported:
                payload = terminus_completion()
                details = payload['usage']['completion_tokens_details']
                if count is None:
                    del details['reasoning_tokens']
                else:
                    details['reasoning_tokens'] = count
                payloads.append(payload)
            endpoint.responses = payloads
            system = make_system(
                harness='terminus-2', max_output_tokens=32768, context_window=131072
            )

            _, collected = run_and_collect(
                system, endpoint, terminal_session, tmp_path, families_dir, data_dir
            )

            assert len(endpoint.requests) == 2
            assert collected.output_tokens == 80
            assert collected.reasoning_tokens == total

        def test_failed_native_attempts_keep_their_artifacts_when_the_caller_retries(
            self,
            endpoint: StubEndpoint,
            messages_agent: NativeProbe,
            tmp_path: Path,
        ) -> None:
            partial = message('Partial.', message_id='partial')
            completed = message('Recovered.', message_id='recovered')
            endpoint.streams = [
                message_stream(partial)[:-1],
                message_stream(completed),
            ]

            async def retry() -> LLMResponse:
                with pytest.raises(MessageStreamError):
                    await messages_agent.client.call('Greet me.')
                return await messages_agent.client.call('Greet me.')

            result = asyncio.run(retry())

            assert result.content == 'Recovered.'
            assert [request.body['messages'] for request in endpoint.requests] == [
                [{'role': 'user', 'content': 'Greet me.'}]
            ] * 2
            assert [
                json.loads((tmp_path / f'message-{ordinal}.json').read_text())['id']
                for ordinal in (1, 2)
            ] == ['partial', 'recovered']
            assert isinstance(messages_agent.client, MessagesLLM)
            assert [
                attempt['response_status'] for attempt in messages_agent.client.attempts
            ] == ['incomplete', 'completed']
            assert [
                recorded['response_id'] for recorded in messages_agent.client.results
            ] == ['recovered']

        @pytest.mark.parametrize(
            ('error_type', 'message', 'expected'),
            [
                (
                    'invalid_request_error',
                    'prompt is too long: 9000 tokens > 8192 maximum',
                    ContextLengthExceededError,
                ),
                (
                    'invalid_request_error',
                    'max_tokens must be positive',
                    anthropic.BadRequestError,
                ),
                (
                    'permission_error',
                    'prompt is too long: 9000 tokens > 8192 maximum',
                    anthropic.BadRequestError,
                ),
            ],
            ids=['context-overflow', 'other-invalid-request', 'other-error-type'],
        )
        def test_only_native_context_overflow_enters_harbor_context_management(
            self,
            endpoint: StubEndpoint,
            messages_agent: NativeProbe,
            error_type: str,
            message: str,
            expected: type[Exception],
        ) -> None:
            endpoint.status = 400
            endpoint.response = {
                'type': 'error',
                'error': {'type': error_type, 'message': message},
                'request_id': 'req-context',
            }

            with pytest.raises(expected):
                asyncio.run(messages_agent.request('Greet me.'))

            assert len(endpoint.requests) == 1
            assert isinstance(messages_agent.client, MessagesLLM)
            assert messages_agent.client.results == []
            (attempt,) = messages_agent.client.attempts
            assert attempt['response_status'] == 'incomplete'
            assert 'input_tokens' not in attempt

        @pytest.mark.parametrize(
            ('api', 'scenario'),
            [
                ('openai-chat-completions', 'negative-input'),
                ('openai-responses', 'negative-input'),
                ('anthropic-messages', 'negative-input'),
                ('openai-chat-completions', 'negative-write'),
                ('openai-responses', 'negative-write'),
            ],
        )
        def test_invalid_sdk_usage_preserves_the_answer_without_entering_accounting(
            self, endpoint: StubEndpoint, tmp_path: Path, api: Api, scenario: str
        ) -> None:
            payload = (
                message()
                if api == 'anthropic-messages'
                else response()
                if api == 'openai-responses'
                else completion()
            )
            if scenario == 'negative-input':
                payload['usage'][
                    'prompt_tokens'
                    if api == 'openai-chat-completions'
                    else 'input_tokens'
                ] = -1
            else:
                payload['usage'][
                    'prompt_tokens_details'
                    if api == 'openai-chat-completions'
                    else 'input_tokens_details'
                ]['cache_write_tokens'] = -1
            if api == 'anthropic-messages':
                endpoint.stream = message_stream(payload)
            else:
                endpoint.response = payload
            system = make_system(
                harness='terminus-2',
                api=api,
                thinking_mode='anthropic-adaptive'
                if api == 'anthropic-messages'
                else None,
            )
            agent = NativeProbe(
                logs_dir=tmp_path,
                model_name=f'openai/{system.route}',
                extra_env=endpoint.env,
                **agent_kwargs(system),
            )

            result = asyncio.run(agent.request('Greet me.'))

            assert result.content == 'Hello there.'
            assert result.usage is None
            assert len(endpoint.requests) == 1
            (recorded,) = agent.client.results
            assert 'input_tokens' not in recorded
            assert recorded['usage_invalid'] is True

        @pytest.mark.parametrize(
            ('stop', 'details', 'tool', 'finish'),
            [
                (
                    'refusal',
                    {
                        'type': 'refusal',
                        'category': 'cyber',
                        'explanation': 'Exploit development.',
                    },
                    False,
                    'refusal',
                ),
                ('pause_turn', None, False, 'pause_turn'),
                ('stop_sequence', None, False, 'stop_sequence'),
                ('tool_use', None, True, 'unexpected_content'),
            ],
            ids=['refusal', 'pause', 'stop-sequence', 'tool'],
        )
        def test_native_completion_failure_names_its_cause_without_quality_retries(
            self,
            endpoint: StubEndpoint,
            terminal_session: TmuxSession,
            tmp_path: Path,
            families_dir: Path,
            data_dir: Path,
            stop: str,
            details: dict[str, Any] | None,
            tool: bool,
            finish: str,
        ) -> None:
            payload = message('Cannot answer.', stop_reason=stop, stop_details=details)
            if tool:
                payload['content'].append(
                    {
                        'type': 'tool_use',
                        'id': 'tool-1',
                        'name': 'read_file',
                        'input': {'path': 'greeting.txt'},
                    }
                )
            endpoint.stream = message_stream(payload)
            system = make_system(
                harness='terminus-2',
                api='anthropic-messages',
                thinking_mode='anthropic-adaptive',
                max_output_tokens=32768,
                context_window=131072,
            )

            context, collected = run_and_collect(
                system,
                endpoint,
                terminal_session,
                tmp_path,
                families_dir,
                data_dir,
                MessageCompletionError,
            )

            assert len(endpoint.requests) == 1
            assert collected.error == 'MessageCompletionError'
            assert collected.finish_reason == finish
            assert collected.stop_details == (
                StopDetails.model_validate(details) if details else None
            )
            assert (
                context.n_input_tokens,
                context.n_cache_tokens,
                context.n_output_tokens,
            ) == (130, 100, 40)

        @pytest.mark.parametrize(
            ('usage', 'tokens', 'cost'),
            [
                ('reported', (130, 100, 40), 0.000485),
                ('zero', (0, 0, 0), 0.0),
                ('missing', (None, None, None), None),
                ('ttl-unpriced', (130, 100, 40), None),
            ],
            ids=['reported-first', 'zero-first', 'unknown-first', 'unpriced-cache-ttl'],
        )
        def test_native_cancellation_keeps_completed_usage_through_collection(
            self,
            endpoint: StubEndpoint,
            messages_agent: NativeProbe,
            messages_clients: list[AsyncAnthropic],
            terminal_session: TmuxSession,
            families_dir: Path,
            data_dir: Path,
            usage: str,
            tokens: tuple[int | None, ...],
            cost: float | None,
        ) -> None:
            text = terminus_completion(task_complete=False)['choices'][0]['message'][
                'content'
            ]
            first = message(text)
            if usage == 'zero':
                first['usage'] = dict.fromkeys(first['usage'], 0)
            elif usage == 'missing':
                first['usage'] = {}
            elif usage == 'ttl-unpriced':
                first['usage']['cache_creation'] = {
                    'ephemeral_1h_input_tokens': 10,
                    'ephemeral_5m_input_tokens': 0,
                }
            endpoint.streams = [message_stream(first)]
            gate = endpoint.response_gate = threading.Event()
            endpoint.response_gate_after = 1
            system = make_system(
                harness='terminus-2',
                api='anthropic-messages',
                thinking_mode='anthropic-adaptive',
                effort='high',
                max_output_tokens=32768,
                context_window=131072,
            )
            write_task_family(families_dir, 'echo', systems=(system.name,))
            family = load_family('f')
            config = jobs.job(family, [system], [])
            context = AgentContext()

            async def cancel() -> None:
                waiting = asyncio.create_task(
                    asyncio.to_thread(endpoint.response_waiting.wait)
                )
                task = asyncio.create_task(
                    messages_agent.execute(terminal_session, context)
                )
                try:
                    completed, _ = await asyncio.wait(
                        (task, waiting), return_when=asyncio.FIRST_COMPLETED
                    )
                    if task in completed:
                        await task
                        pytest.fail('The agent exited before the second request.')
                    await waiting
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await asyncio.wait_for(task, 5)
                finally:
                    gate.set()
                    task.cancel()
                    endpoint.response_waiting.set()
                    await asyncio.gather(task, waiting, return_exceptions=True)

            asyncio.run(cancel())
            job_dir = data_dir / 'jobs' / family.name / 'cancelled'
            write_trial(
                job_dir,
                'echo__cancelled',
                config.agents[0],
                task='f/echo',
                version='1.0.0',
                context=context,
                error='CancelledError',
                instruction=None,
                environment=config.environment,
                verifier_disabled=config.verifier.disable,
            )
            (collected,) = jobs.collect(family, {system.name: system})

            assert (
                context.n_input_tokens,
                context.n_cache_tokens,
                context.n_output_tokens,
            ) == tokens
            assert (
                collected.input_tokens,
                collected.cached_tokens,
                collected.output_tokens,
            ) == tokens
            assert collected.cache_write_tokens == (
                10
                if usage in ('reported', 'ttl-unpriced')
                else 0
                if usage == 'zero'
                else None
            )
            assert collected.cost_usd == (
                pytest.approx(cost) if cost is not None else None
            )
            assert context.metadata is not None
            assert [
                call['response_status'] for call in context.metadata['responses']
            ] == ['completed']
            assert [
                call['response_status'] for call in context.metadata['message_attempts']
            ] == ['completed', 'incomplete']
            (client,) = messages_clients
            assert client.is_closed()
            assert len(endpoint.requests) == 2

        def test_native_budget_continuation_keeps_signed_blocks_and_all_usage(
            self,
            endpoint: StubEndpoint,
            messages_agent: NativeProbe,
            terminal_session: TmuxSession,
        ) -> None:
            partial = message(
                '{"analysis": "partial', message_id='partial', stop_reason='max_tokens'
            )
            text = terminus_completion()['choices'][0]['message']['content']
            endpoint.streams = [
                message_stream(payload)
                for payload in (
                    partial,
                    message(text, message_id='finished'),
                    message(text, message_id='confirmed'),
                )
            ]
            context = AgentContext()

            asyncio.run(messages_agent.execute(terminal_session, context))

            first, reissued, confirmed = endpoint.requests
            assert reissued.body['messages'][:-1] == [
                *first.body['messages'],
                {'role': 'assistant', 'content': partial['content']},
            ]
            assert '32768 tokens' in reissued.body['messages'][-1]['content']
            assert (
                confirmed.body['messages'][: len(reissued.body['messages'])]
                == reissued.body['messages']
            )
            assert (
                context.n_input_tokens,
                context.n_cache_tokens,
                context.n_output_tokens,
            ) == (390, 300, 120)
            assert context.metadata is not None
            assert context.metadata['cache_write_tokens'] == 30
            assert [
                call['finish_reason'] for call in context.metadata['responses']
            ] == ['length', 'stop', 'stop']
            assert all(
                request.body['output_config'] == {'effort': 'high'}
                for request in endpoint.requests
            )
            assert all('tools' not in request.body for request in endpoint.requests)

        def test_counts_native_chat_summary_subagents_once_with_main_requests(
            self,
            endpoint: StubEndpoint,
            monkeypatch: pytest.MonkeyPatch,
            terminal_session: TmuxSession,
            tmp_path: Path,
        ) -> None:
            endpoint.responses = [
                terminus_completion(task_complete=False),
                completion('Summary.'),
                completion('Questions.'),
                completion('Answers.'),
                terminus_completion(),
                terminus_completion(),
            ]
            system = make_system(
                harness='terminus-2', max_output_tokens=32768, context_window=131072
            )
            api_log = tmp_path / 'api.json'
            kwargs = agent_kwargs(system)
            kwargs['llm_call_kwargs']['logging_path'] = api_log
            agent = NativeProbe(
                logs_dir=tmp_path,
                model_name=f'openai/{system.route}',
                extra_env=endpoint.env,
                **kwargs,
            )
            context = AgentContext()

            async def summarize_once(
                self: Terminus2,
                chat: Chat,
                original_instruction: str,
                session: TmuxSession,
            ) -> tuple[str, list[SubagentTrajectoryRef] | None] | None:
                if len(endpoint.requests) == 1:
                    assert isinstance(self, NativeProbe)
                    return await self.summarize_result(
                        chat, original_instruction, session
                    )
                return None

            monkeypatch.setattr(
                Terminus2, '_check_proactive_summarization', summarize_once
            )

            asyncio.run(agent.execute(terminal_session, context))

            _, _, questions, answers, continued, confirmed = endpoint.requests
            assert 'Summary.' in questions.body['messages'][-1]['content']
            assert 'Questions.' in answers.body['messages'][-1]['content']
            assert 'Answers.' in continued.body['messages'][-1]['content']
            assert (
                confirmed.body['messages'][: len(continued.body['messages'])]
                == continued.body['messages']
            )
            assert (
                context.n_input_tokens,
                context.n_cache_tokens,
                context.n_output_tokens,
            ) == (720, 600, 240)
            assert context.metadata is not None
            assert context.metadata['summarization_count'] == 1
            assert [
                tuple(
                    result[key]
                    for key in ('input_tokens', 'cache_tokens', 'output_tokens')
                )
                for result in context.metadata['responses']
            ] == [(120, 100, 40)] * 6
            logged = json.loads(api_log.read_text())
            assert logged['log_event_type'] == 'post_api_call'
            assert ChatCompletion.model_validate_json(
                logged['original_response']
            ) == ChatCompletion.model_validate(terminus_completion())

        def test_retryable_chat_errors_keep_native_attempts_without_sdk_retries(
            self,
            endpoint: StubEndpoint,
            endpoint_clients: list[openai.AsyncOpenAI],
            tmp_path: Path,
        ) -> None:
            endpoint.status = 503
            endpoint.response = {'error': {'message': 'Unavailable.'}}
            system = make_system(harness='terminus-2')
            agent = NativeProbe(
                logs_dir=tmp_path,
                model_name=f'openai/{system.route}',
                extra_env=endpoint.env,
                **agent_kwargs(system),
            )

            with pytest.raises(ServiceUnavailableError):
                asyncio.run(agent.request('Greet me.'))

            assert [request.path for request in endpoint.requests] == [
                '/v1/chat/completions'
            ] * 3
            assert [
                request.headers['x-stainless-retry-count']
                for request in endpoint.requests
            ] == ['0', '0', '0']
            (client,) = endpoint_clients
            assert client.max_retries == 0
            assert client.is_closed()

        @pytest.mark.parametrize(
            ('prior_usage', 'tokens', 'cost'),
            [
                (None, (None, None, None), None),
                ('reported', (120, 100, 40), 0.00046),
                ('zero', (0, 0, 0), 0.0),
                ('null', (None, None, None), None),
                ('omitted', (None, None, None), None),
            ],
            ids=[
                'before-first',
                'reported-first',
                'zero-first',
                'null-first',
                'omitted-first',
            ],
        )
        @pytest.mark.parametrize('api', ['openai-chat-completions', 'openai-responses'])
        def test_cancellation_preserves_reported_native_usage_and_closes_the_request(
            self,
            endpoint: StubEndpoint,
            endpoint_clients: list[openai.AsyncOpenAI],
            export: NixosConfigExport,
            families_dir: Path,
            data_dir: Path,
            model_requests: list[httpx.Request],
            terminal_session: TmuxSession,
            tmp_path: Path,
            api: str,
            prior_usage: str | None,
            tokens: tuple[int | None, int | None, int | None],
            cost: float | None,
        ) -> None:
            gate = endpoint.response_gate = threading.Event()
            if prior_usage is not None:
                first = terminus_completion(task_complete=False)
                if api == 'openai-responses':
                    first = response(first['choices'][0]['message']['content'])
                if prior_usage == 'omitted':
                    first.pop('usage')
                elif prior_usage == 'null':
                    first['usage'] = None
                elif prior_usage == 'zero':
                    if api == 'openai-responses':
                        first['usage'] = {
                            'input_tokens': 0,
                            'input_tokens_details': {'cached_tokens': 0},
                            'output_tokens': 0,
                            'output_tokens_details': {'reasoning_tokens': 0},
                            'total_tokens': 0,
                        }
                    else:
                        first['usage'] = {
                            'prompt_tokens': 0,
                            'completion_tokens': 0,
                            'total_tokens': 0,
                            'prompt_tokens_details': {'cached_tokens': 0},
                            'completion_tokens_details': {'reasoning_tokens': 0},
                        }
                endpoint.responses = [first]
                endpoint.response_gate_after = 1
            system = make_system(
                harness='terminus-2',
                api=api,
                max_output_tokens=32768,
                context_window=131072,
            )
            write_task_family(families_dir, 'echo', systems=(system.name,))
            family = load_family('f')
            config = jobs.job(family, [system], [])
            agent = NativeProbe(
                logs_dir=tmp_path,
                model_name=f'openai/{system.route}',
                extra_env=endpoint.env,
                **agent_kwargs(system),
            )
            context = AgentContext()

            async def cancel() -> str:
                task = asyncio.create_task(agent.execute(terminal_session, context))
                try:
                    assert await asyncio.to_thread(endpoint.response_waiting.wait, 5)
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError) as error:
                        await asyncio.wait_for(task, 5)
                    return type(error.value).__name__
                finally:
                    gate.set()
                    if not task.done():
                        task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

            error = asyncio.run(cancel())
            job_dir = data_dir / 'jobs' / family.name / 'cancelled'
            write_trial(
                job_dir,
                'echo__cancelled',
                config.agents[0],
                task='f/echo',
                version='1.0.0',
                context=context,
                error=error,
                instruction=None,
                environment=config.environment,
                verifier_disabled=config.verifier.disable,
            )
            reread = jobs.read_trials(family, job_dir, {system.name: system})
            collected = jobs.collect(family, {system.name: system})

            path = (
                '/v1/responses' if api == 'openai-responses' else '/v1/chat/completions'
            )
            assert [request.path for request in endpoint.requests] == [path] * (
                1 if prior_usage is None else 2
            )
            (client,) = endpoint_clients
            assert client.is_closed()
            assert context.cost_usd is None
            if prior_usage is None:
                assert context.rollout_details == []
            assert context.metadata is not None
            assert context.metadata['nixos_config_rev'] == export.rev
            assert context.metadata['harbor_version'] == metadata.version('harbor')
            assert len(context.metadata['api_request_times_msec']) == (
                0 if prior_usage is None else 1
            )
            (recorded,) = reread
            (current,) = collected
            for output in (recorded, current):
                assert output.error == 'CancelledError'
                assert (
                    output.input_tokens,
                    output.cached_tokens,
                    output.output_tokens,
                ) == tokens
                if cost is None:
                    assert output.cost_usd is None
                else:
                    assert output.cost_usd == pytest.approx(cost)
            assert (
                context.n_input_tokens,
                context.n_cache_tokens,
                context.n_output_tokens,
            ) == tokens
            if api == 'openai-responses':
                assert [
                    result['response_status']
                    for result in context.metadata['responses']
                ] == ([] if prior_usage is None else ['completed'])
                assert sorted(
                    path.name for path in tmp_path.glob('response-*.json')
                ) == ([] if prior_usage is None else ['response-1.json'])
            trajectory = json.loads((tmp_path / 'trajectory.json').read_text())
            assert [
                step['message']
                for step in trajectory['steps']
                if step['source'] == 'agent'
            ] == ([] if prior_usage is None else [''])
            assert [sent.extensions['timeout'] for sent in model_requests] == [
                {'connect': 30.0, 'read': None, 'write': 600.0, 'pool': 600.0}
            ] * (1 if prior_usage is None else 2)
            assert client.max_retries == 0

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
            assert context.metadata['responses'] == []
            assert [
                call['response_status']
                for call in context.metadata['response_attempts']
            ] == ['request_failed'] * 3

        def test_counts_usage_from_completed_requests_after_a_retry(
            self,
            endpoint: StubEndpoint,
            responses_agent: NativeProbe,
            terminal_session: TmuxSession,
        ) -> None:
            endpoint.statuses = [503]
            endpoint.response = response(
                json.dumps(
                    {'analysis': '', 'plan': '', 'commands': [], 'task_complete': True}
                )
            )
            context = AgentContext()

            asyncio.run(responses_agent.execute(terminal_session, context))

            assert len(endpoint.requests) == 3
            assert (
                context.n_input_tokens,
                context.n_cache_tokens,
                context.n_output_tokens,
                context.cost_usd,
            ) == (240, 200, 80, None)
            assert context.metadata is not None
            assert [
                call['response_status'] for call in context.metadata['responses']
            ] == ['completed', 'completed']
            assert [
                call['response_status']
                for call in context.metadata['response_attempts']
            ] == ['request_failed', 'completed', 'completed']

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

        def test_records_a_recovered_context_error_as_a_failed_attempt(
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
                asyncio.run(responses_agent.call('Greet me.'))

            endpoint.status = 200
            endpoint.response = response('Hello there.')

            reply = asyncio.run(responses_agent.call('Greet me again.'))

            assert reply.usage is not None
            llm = responses_agent.client
            assert isinstance(llm, ResponsesLLM)
            assert [attempt['response_status'] for attempt in llm.attempts] == [
                'request_failed',
                'completed',
            ]
            assert [result['response_status'] for result in llm.results] == [
                'completed'
            ]

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

        @pytest.mark.parametrize(
            'missing_usage', [False, True], ids=['reported', 'omitted']
        )
        def test_chat_budget_reissue_preserves_partial_text_and_reported_usage(
            self,
            endpoint: StubEndpoint,
            terminal_session: TmuxSession,
            tmp_path: Path,
            missing_usage: bool,
        ) -> None:
            partial_text = '{"analysis": "partial'
            partial = completion(partial_text, finish_reason='length')
            if missing_usage:
                partial.pop('usage')
            endpoint.responses = [partial, terminus_completion(), terminus_completion()]
            system = make_system(
                harness='terminus-2', max_output_tokens=32768, context_window=131072
            )
            agent = NativeProbe(
                logs_dir=tmp_path,
                model_name=f'openai/{system.route}',
                extra_env=endpoint.env,
                **agent_kwargs(system),
            )
            context = AgentContext()

            asyncio.run(agent.execute(terminal_session, context))

            first, reissued, confirmed = endpoint.requests
            assert reissued.body['messages'][:-2] == first.body['messages']
            assert reissued.body['messages'][-2] == {
                'role': 'assistant',
                'content': partial_text,
            }
            assert reissued.body['messages'][-1]['role'] == 'user'
            assert '32768 tokens' in reissued.body['messages'][-1]['content']
            assert (
                confirmed.body['messages'][: len(reissued.body['messages'])]
                == reissued.body['messages']
            )
            assert (
                context.n_input_tokens,
                context.n_cache_tokens,
                context.n_output_tokens,
            ) == ((None, None, None) if missing_usage else (360, 300, 120))
            assert context.cost_usd is None
            assert context.metadata is not None
            recorded, reissued_usage, confirmed_usage = context.metadata['responses']
            assert ('input_tokens' not in recorded) == missing_usage
            if not missing_usage:
                assert tuple(
                    recorded[key]
                    for key in ('input_tokens', 'cache_tokens', 'output_tokens')
                ) == (120, 100, 40)
            assert reissued_usage == confirmed_usage
            assert tuple(
                reissued_usage[key]
                for key in ('input_tokens', 'cache_tokens', 'output_tokens')
            ) == (120, 100, 40)

        @pytest.mark.parametrize(
            'object_type',
            [None, 'unexpected'],
            ids=['missing-object', 'unexpected-object'],
        )
        def test_keeps_usage_unknown_for_an_unrecognized_completion_envelope(
            self,
            endpoint: StubEndpoint,
            terminal_session: TmuxSession,
            tmp_path: Path,
            object_type: str | None,
        ) -> None:
            second = terminus_completion()
            if object_type is None:
                second.pop('object')
            else:
                second['object'] = object_type
            endpoint.responses = [terminus_completion(), second]
            system = make_system(
                harness='terminus-2', max_output_tokens=32768, context_window=131072
            )
            agent = NativeProbe(
                logs_dir=tmp_path,
                model_name=f'openai/{system.route}',
                extra_env=endpoint.env,
                **agent_kwargs(system),
            )
            context = AgentContext()

            asyncio.run(agent.execute(terminal_session, context))

            assert len(endpoint.requests) == 2
            assert (
                context.n_input_tokens,
                context.n_cache_tokens,
                context.n_output_tokens,
                context.cost_usd,
            ) == (None, None, None, None)
            assert context.metadata is not None
            first, second = context.metadata['responses']
            assert tuple(
                first[key] for key in ('input_tokens', 'cache_tokens', 'output_tokens')
            ) == (120, 100, 40)
            assert second == {}

        @pytest.mark.parametrize(
            ('api', 'usage'),
            [
                ('openai-chat-completions', 'omitted'),
                ('openai-responses', 'omitted'),
                ('openai-chat-completions', 'incomplete'),
                ('openai-chat-completions', 'negative'),
            ],
            ids=[
                'chat-omitted',
                'responses-omitted',
                'chat-incomplete',
                'chat-negative',
            ],
        )
        @pytest.mark.parametrize('unknown', [0, 1], ids=['first', 'last'])
        def test_keeps_aggregate_usage_unknown_if_any_native_response_lacks_valid_usage(
            self,
            endpoint: StubEndpoint,
            terminal_session: TmuxSession,
            tmp_path: Path,
            api: str,
            usage: str,
            unknown: int,
        ) -> None:
            text = terminus_completion()['choices'][0]['message']['content']
            endpoint.responses = (
                [
                    reasoned_response(text, 'first'),
                    reasoned_response(text, 'last', 'final_answer'),
                ]
                if api == 'openai-responses'
                else [terminus_completion(), terminus_completion()]
            )
            if usage == 'omitted':
                endpoint.responses[unknown].pop('usage')
            elif usage == 'negative':
                endpoint.responses[unknown]['usage']['prompt_tokens'] = -1
            else:
                endpoint.responses[unknown]['usage'] = {'total_tokens': 160}
            if api == 'openai-chat-completions':
                endpoint.response = terminus_completion()
            system = make_system(
                harness='terminus-2',
                api=api,
                max_output_tokens=32768,
                context_window=131072,
            )
            agent = NativeProbe(
                logs_dir=tmp_path,
                model_name=f'openai/{system.route}',
                extra_env=endpoint.env,
                **agent_kwargs(system),
            )
            context = AgentContext()

            asyncio.run(agent.execute(terminal_session, context))

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
            ('status', 'reason', 'refusal', 'error', 'tool', 'finish'),
            [
                ('failed', None, None, True, False, 'error'),
                ('queued', None, None, False, False, 'queued'),
                ('cancelled', None, None, False, False, 'cancelled'),
                ('incomplete', 'content_filter', None, False, False, 'content_filter'),
                ('incomplete', None, None, False, False, 'incomplete'),
                ('completed', None, 'I cannot answer.', False, False, 'refusal'),
                ('completed', None, None, True, False, 'error'),
                ('completed', None, None, False, True, 'unexpected_content'),
            ],
            ids=[
                'failed',
                'queued',
                'cancelled',
                'filtered',
                'unknown-incomplete',
                'refused',
                'error-with-completed-status',
                'function-call',
            ],
        )
        def test_rejects_unusable_native_responses_without_retrying(
            self,
            endpoint: StubEndpoint,
            terminal_session: TmuxSession,
            tmp_path: Path,
            families_dir: Path,
            data_dir: Path,
            status: str,
            reason: str | None,
            refusal: str | None,
            error: bool,
            tool: bool,
            finish: str,
        ) -> None:
            payload = response(
                'Partial answer.',
                status=status,
                incomplete_reason=reason,
                refusal=refusal,
            )
            if error:
                payload['error'] = {'code': 'server_error', 'message': 'Unavailable.'}
            if tool:
                payload['output'].append(
                    {
                        'id': 'fc-1',
                        'type': 'function_call',
                        'call_id': 'call-1',
                        'name': 'read_file',
                        'arguments': '{"path":"greeting.txt"}',
                        'status': 'completed',
                    }
                )
            Response.model_validate(payload)
            endpoint.response = payload
            system = make_system(
                harness='terminus-2',
                api='openai-responses',
                max_output_tokens=32768,
                context_window=131072,
            )

            context, collected = run_and_collect(
                system,
                endpoint,
                terminal_session,
                tmp_path,
                families_dir,
                data_dir,
                ResponseCompletionError,
            )

            assert len(endpoint.requests) == 1
            assert collected.error == 'ResponseCompletionError'
            assert collected.finish_reason == finish
            assert collected.stop_details is None
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
                **agent_kwargs(system),
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
                **agent_kwargs(system),
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
                **agent_kwargs(system),
            )

            result = asyncio.run(agent.request('Greet me.'))

            (request,) = endpoint.requests
            prompt = [{'role': 'user', 'content': 'Greet me.'}]
            if api == 'openai-responses':
                assert request.path == '/v1/responses'
                assert request.body == {
                    'model': system.route,
                    'input': prompt,
                    'reasoning': {'effort': effort},
                    'max_output_tokens': 32768,
                    'store': False,
                    'temperature': 0.7,
                }
            else:
                assert request.path == '/v1/chat/completions'
                assert request.body == {
                    'model': system.route,
                    'messages': prompt,
                    'reasoning_effort': effort,
                    'max_completion_tokens': 32768,
                    'temperature': 0.7,
                }
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
                **agent_kwargs(system),
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
            kwargs = agent_kwargs(make_system(harness='terminus-2', api=api))
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
            expected: dict[str, Any] = {
                'n_episodes': 1,
                'nixos_config_rev': export.rev,
                'harbor_version': metadata.version('harbor'),
                'finish_reason': None,
                'stop_details': None,
                'responses': [],
                'cache_write_unpriced': False,
            }
            if api == 'openai-responses':
                expected['response_attempts'] = []
            assert context.metadata == expected
