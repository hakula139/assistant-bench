"""Harbor's terminus-2 agent, sending its model calls to the bench endpoint."""

from collections.abc import Mapping
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any, ClassVar, Self, assert_never, override

from anthropic import AsyncAnthropic
from harbor.agents.agent_auth_resolution import ResolvedModelConnection
from harbor.agents.terminus_2.terminus_2 import Terminus2, Terminus2Options
from harbor.agents.terminus_2.tmux_session import TmuxSession
from harbor.environments.base import BaseEnvironment
from harbor.llms.base import BaseLLM, LLMBackend, LLMResponse
from harbor.llms.chat import Chat
from harbor.llms.lite_llm import LiteLLM
from harbor.llms.litellm_config import configure_litellm_debug
from harbor.models.agent.context import AgentContext
from harbor.models.metric import UsageInfo
from harbor.models.trajectories import Step, SubagentTrajectoryRef
from openai import AsyncOpenAI
from pydantic import TypeAdapter
from tenacity import retry_if_not_exception_type

from assistant_bench.catalog import load_export
from assistant_bench.generation import load_generation
from assistant_bench.harnesses.endpoint import Endpoint, preflight
from assistant_bench.harnesses.labels import Labels
from assistant_bench.harnesses.messages import MessagesLLM
from assistant_bench.harnesses.native import (
    NativeChat,
    NativeCompletionError,
    NativeLLM,
    NativeSettings,
    attach_items,
)
from assistant_bench.harnesses.options import ApiOptions
from assistant_bench.harnesses.responses import ResponsesLLM
from assistant_bench.harnesses.usage import (
    MeasuredUsage,
    append_attempt,
    measure,
    record_calls,
)


_RESPONSE = TypeAdapter(dict[str, Any])


class TerminusOptions(Terminus2Options, Labels, ApiOptions):
    pass


class ChatCompletionsLLM(LiteLLM):
    """Keep resolved capacities per instance, outside LiteLLM's global registry."""

    _context_limit: int
    _output_limit: int
    calls: list[dict[str, Any]]

    @classmethod
    def configured(
        cls, model_name: str, context_limit: int, output_limit: int, **kwargs: Any
    ) -> Self:
        llm = cls(model_name=model_name, **kwargs)
        llm._context_limit = context_limit
        llm._output_limit = output_limit
        llm.calls = []
        return llm

    @override
    def get_model_context_limit(self) -> int:
        return self._context_limit

    @override
    def get_model_output_limit(self) -> int:
        return self._output_limit

    @override
    def _build_base_kwargs(self, logging_path: Path | None = None) -> dict[str, Any]:
        kwargs = super()._build_base_kwargs(logging_path)
        logger_fn = kwargs['logger_fn']

        def record_usage(details: dict[str, Any]) -> None:
            try:
                self._record_usage(details)
            finally:
                if logger_fn is not None:
                    logger_fn(details)

        kwargs['logger_fn'] = record_usage
        return kwargs

    def _record_usage(self, details: dict[str, Any]) -> None:
        if details.get('log_event_type') != 'post_api_call':
            return
        response = _RESPONSE.validate_json(details['original_response'])
        if response.get('error') is not None and 'choices' not in response:
            return

        call: dict[str, Any] = {
            'responded': True,
            'response_model': response.get('model'),
        }
        self.calls.append(call)
        if (
            response.get('object') == 'chat.completion'
            and response.get('usage') is not None
        ):
            call.update(measure('openai-chat-completions', response['usage']))

    @override
    def _extract_usage_info(self, response: Any) -> UsageInfo | None:
        """Use original SDK counters before LiteLLM fills absent values."""
        usage = MeasuredUsage.from_details(self.calls[-1]) if self.calls else None
        return usage.harbor if usage is not None else None


class Terminus(Terminus2):
    """Harbor's `Terminus2` through the bench's endpoint-bound native clients.

    The client carries the key file, the CA bundle, the extra headers, and the
    local address, so none of them enters the job's `kwargs`, `env`, or config.
    """

    options_model: ClassVar[type[Terminus2Options]] = TerminusOptions
    _endpoint_client: AsyncOpenAI | AsyncAnthropic
    _summary_replies: dict[str, LLMResponse]

    @override
    def version(self) -> str:
        assert isinstance(self.options, TerminusOptions)
        return load_generation().version(True, self.options.api)

    @classmethod
    @override
    def check_model_credentials(
        cls,
        connection: ResolvedModelConnection,
        *,
        kwargs: dict[str, Any],
        env: Mapping[str, str],
        explicit_env: Mapping[str, str] | None = None,
    ) -> None:
        preflight(env, ApiOptions.model_validate(kwargs).api)

    @override
    def _init_llm(
        self,
        llm_backend: LLMBackend | str,
        model_name: str,
        temperature: float | None,
        collect_rollout_details: bool,
        llm_kwargs: dict[str, Any] | None,
        api_base: str | None,
        session_id: str | None,
        max_thinking_tokens: int | None,
        reasoning_effort: str | None,
        model_info: dict[str, Any] | None,
        use_responses_api: bool,
    ) -> BaseLLM:
        assert isinstance(self.options, TerminusOptions)
        assert model_info is not None
        endpoint = Endpoint.read(self._get_env, self.options.api)
        match self.options.api:
            case 'anthropic-messages':
                self._endpoint_client = endpoint.messages_client()
                return MessagesLLM(
                    self._endpoint_client,
                    self._native_settings(model_name, reasoning_effort, model_info),
                )
            case 'openai-responses':
                self._endpoint_client = endpoint.client()
                return ResponsesLLM(
                    self._endpoint_client,
                    self._native_settings(model_name, reasoning_effort, model_info),
                )
            case 'openai-chat-completions':
                self._endpoint_client = endpoint.client()
                constructor_kwargs: dict[str, Any] = {
                    **(llm_kwargs or {}),
                    'client': self._endpoint_client,
                    'max_retries': self._endpoint_client.max_retries,
                    'timeout': self._endpoint_client.timeout,
                }
                configure_litellm_debug(
                    debug=constructor_kwargs.pop('litellm_debug', False)
                )
                return ChatCompletionsLLM.configured(
                    # Pins LiteLLM to its OpenAI-compatible provider.
                    model_name=f'openai/{model_name}',
                    api_base=str(endpoint.base_url),
                    collect_rollout_details=collect_rollout_details,
                    session_id=session_id,
                    max_thinking_tokens=max_thinking_tokens,
                    reasoning_effort=reasoning_effort,
                    context_limit=model_info['max_input_tokens'],
                    output_limit=model_info['max_output_tokens'],
                    **constructor_kwargs,
                )
            case _:
                assert_never(self.options.api)

    def _native_settings(
        self, model_name: str, effort: str | None, model_info: dict[str, Any]
    ) -> NativeSettings:
        return NativeSettings(
            model_name=model_name,
            context_limit=model_info['max_input_tokens'],
            output_limit=model_info['max_output_tokens'],
            effort=effort,
            logs_dir=self.logs_dir,
        )

    _query_llm = Terminus2._query_llm.retry_with(
        retry=Terminus2._query_llm.retry.retry
        & retry_if_not_exception_type(NativeCompletionError)
    )

    @override
    async def _run_agent_loop(
        self, initial_prompt: str, chat: Chat, original_instruction: str = ''
    ) -> None:
        if isinstance(self._llm, NativeLLM):
            chat = NativeChat(
                self._llm, interleaved_thinking=self.options.interleaved_thinking
            )
            self._chat = chat
        await super()._run_agent_loop(initial_prompt, chat, original_instruction)

    @override
    async def _run_subagent(
        self,
        prompt: str,
        message_history: list[dict[str, Any]],
        steps: list[Step],
        session_id: str,
        agent_name: str,
        filename_suffix: str,
        summary_text: str,
        subagent_name_for_logging: str = 'subagent',
    ) -> tuple[LLMResponse, SubagentTrajectoryRef]:
        """Retain SDK items when Harbor builds the answers branch from its summary."""
        native = isinstance(self._llm, NativeLLM)
        if native and filename_suffix == 'answers':
            attach_items(message_history[-1], self._summary_replies['summary'])
        response, trajectory = await super()._run_subagent(
            prompt,
            message_history,
            steps,
            session_id,
            agent_name,
            filename_suffix,
            summary_text,
            subagent_name_for_logging,
        )
        if native:
            self._summary_replies[filename_suffix] = response
        return response, trajectory

    @override
    async def _summarize(
        self, chat: Chat, original_instruction: str, session: TmuxSession
    ) -> tuple[str, list[SubagentTrajectoryRef] | None]:
        self._summary_replies = {}
        try:
            result = await super()._summarize(chat, original_instruction, session)
            if isinstance(self._llm, NativeLLM) and result[1] is not None:
                attach_items(chat.messages[-1], self._summary_replies['questions'])
            return result
        finally:
            self._summary_replies = {}

    @override
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        assert isinstance(self.options, TerminusOptions)
        started_at = datetime.now(UTC)
        nixos_config_rev = load_export(self._get_env).rev
        harbor_version = metadata.version('harbor')
        llm = self._llm
        assert isinstance(llm, NativeLLM | ChatCompletionsLLM)
        failed_attempt: dict[str, Any] = {}
        try:
            await super().run(instruction, environment, context)
        except NativeCompletionError:
            assert isinstance(llm, NativeLLM)
            failed_attempt = llm.attempts[-1]
            raise
        finally:
            await self._endpoint_client.close()
            context.metadata = {
                **(context.metadata or {}),
                'nixos_config_rev': nixos_config_rev,
                'harbor_version': harbor_version,
                'finish_reason': failed_attempt.get('finish_reason'),
                'stop_details': failed_attempt.get('stop_details'),
            }
            if isinstance(llm, NativeLLM):
                context.metadata['attempts'] = llm.attempts
            calls = llm.calls
            record_calls(context, calls)
            append_attempt(
                self.logs_dir,
                self.options,
                started_at,
                llm.attempts if isinstance(llm, NativeLLM) else calls,
            )
