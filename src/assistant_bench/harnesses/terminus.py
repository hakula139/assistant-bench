"""Harbor's terminus-2 agent, sending its model calls to the bench endpoint."""

from collections.abc import Mapping
from importlib import metadata
from pathlib import Path
from typing import Any, ClassVar, Self, override

from harbor.agents.terminus_2.terminus_2 import Terminus2, Terminus2Options
from harbor.agents.terminus_2.tmux_session import TmuxSession
from harbor.environments.base import BaseEnvironment
from harbor.llms.base import BaseLLM, LLMBackend, LLMResponse
from harbor.llms.chat import Chat
from harbor.llms.lite_llm import LiteLLM
from harbor.llms.litellm_config import configure_litellm_debug
from harbor.models.agent.context import AgentContext
from harbor.models.trajectories import Step, SubagentTrajectoryRef
from openai import AsyncOpenAI
from openai.types.completion_usage import CompletionUsage
from pydantic import ConfigDict, TypeAdapter, ValidationError
from tenacity import retry_if_not_exception_type

from assistant_bench.catalog import load_export
from assistant_bench.generation import generation_version
from assistant_bench.harnesses.endpoint import Endpoint, preflight
from assistant_bench.harnesses.labels import Labels
from assistant_bench.harnesses.responses import (
    ResponseCompletionError,
    ResponsesChat,
    ResponsesLLM,
    attach_items,
)


_RESPONSE_METADATA = TypeAdapter(
    dict[str, Any], config=ConfigDict(hide_input_in_errors=True)
)


class TerminusOptions(Labels, Terminus2Options):
    pass


class _ConfiguredLiteLLM(LiteLLM):
    """Keep resolved capacities per instance, outside LiteLLM's global registry."""

    _context_limit: int
    _output_limit: int
    results: list[dict[str, Any]]

    @classmethod
    def configured(
        cls, model_name: str, context_limit: int, output_limit: int, **kwargs: Any
    ) -> Self:
        llm = cls(model_name=model_name, **kwargs)
        llm._context_limit = context_limit
        llm._output_limit = output_limit
        llm.results = []
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
        try:
            response = _RESPONSE_METADATA.validate_json(details['original_response'])
        except ValidationError:
            return
        if response.get('error') is not None and 'choices' not in response:
            return

        result: dict[str, Any] = {}
        self.results.append(result)
        if response.get('object') != 'chat.completion':
            return
        if response.get('usage') is None:
            return
        try:
            usage = CompletionUsage.model_validate(response['usage'])
        except ValidationError:
            raise ValueError('The provider returned invalid token usage.') from None

        result.update(
            input_tokens=usage.prompt_tokens,
            cache_tokens=(
                usage.prompt_tokens_details.cached_tokens or 0
                if usage.prompt_tokens_details is not None
                else 0
            ),
            output_tokens=usage.completion_tokens,
        )


class Terminus(Terminus2):
    """Harbor's `Terminus2` through the bench's endpoint-bound native clients.

    The client carries the key file, the CA bundle, the extra headers, and the
    local address, so none of them enters the job's `kwargs`, `env`, or config.
    """

    options_model: ClassVar[type[Terminus2Options]] = TerminusOptions
    _endpoint_client: AsyncOpenAI
    _responses_summaries: dict[str, LLMResponse]

    @override
    def version(self) -> str:
        return generation_version('terminus-2')

    @classmethod
    @override
    def preflight(
        cls,
        kwargs: dict[str, Any] | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        super().preflight(kwargs, env)
        preflight(env)

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
        if llm_backend != LLMBackend.LITELLM:
            raise ValueError('The bench endpoint requires the LiteLLM backend.')
        if model_info is None:
            raise ValueError('Terminus requires resolved model capacities.')

        endpoint = Endpoint.read(self._get_env)
        self._endpoint_client = endpoint.client()
        if use_responses_api:
            assert isinstance(self.options, TerminusOptions)
            return ResponsesLLM(
                client=self._endpoint_client,
                model_name=model_name,
                context_limit=model_info['max_input_tokens'],
                output_limit=model_info['max_output_tokens'],
                effort=reasoning_effort,
                temperature=temperature,
                logs_dir=self.logs_dir,
            )

        constructor_kwargs: dict[str, Any] = {
            **(llm_kwargs or {}),
            'client': self._endpoint_client,
            'max_retries': self._endpoint_client.max_retries,
            'timeout': self._endpoint_client.timeout,
        }
        if temperature is not None:
            constructor_kwargs['temperature'] = temperature
        configure_litellm_debug(debug=constructor_kwargs.pop('litellm_debug', False))

        return _ConfiguredLiteLLM.configured(
            context_limit=model_info['max_input_tokens'],
            output_limit=model_info['max_output_tokens'],
            model_name=model_name,
            api_base=str(endpoint.base_url),
            collect_rollout_details=collect_rollout_details,
            session_id=session_id,
            max_thinking_tokens=max_thinking_tokens,
            reasoning_effort=reasoning_effort,
            use_responses_api=use_responses_api,
            **constructor_kwargs,
        )

    _query_llm = Terminus2._query_llm.retry_with(
        retry=Terminus2._query_llm.retry.retry
        & retry_if_not_exception_type(ResponseCompletionError)
    )

    @override
    async def _run_agent_loop(
        self, initial_prompt: str, chat: Chat, original_instruction: str = ''
    ) -> None:
        if isinstance(self._llm, ResponsesLLM):
            responses_chat = ResponsesChat(
                self._llm, interleaved_thinking=self.options.interleaved_thinking
            )
            responses_chat.messages.extend(chat.messages)
            chat = responses_chat
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
        if isinstance(self._llm, ResponsesLLM) and filename_suffix == 'answers':
            attach_items(message_history[-1], self._responses_summaries['summary'])
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
        if isinstance(self._llm, ResponsesLLM):
            self._responses_summaries[filename_suffix] = response
        return response, trajectory

    @override
    async def _summarize(
        self, chat: Chat, original_instruction: str, session: TmuxSession
    ) -> tuple[str, list[SubagentTrajectoryRef] | None]:
        self._responses_summaries = {}
        try:
            result = await super()._summarize(chat, original_instruction, session)
            if isinstance(self._llm, ResponsesLLM) and result[1] is not None:
                attach_items(chat.messages[-1], self._responses_summaries['questions'])
            return result
        finally:
            self._responses_summaries = {}

    @override
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        nixos_config_rev = load_export(self._get_env).rev
        harbor_version = metadata.version('harbor')
        try:
            await super().run(instruction, environment, context)
        finally:
            await self._endpoint_client.close()
            context.metadata = {
                **(context.metadata or {}),
                'nixos_config_rev': nixos_config_rev,
                'harbor_version': harbor_version,
            }
            if isinstance(self._llm, (ResponsesLLM, _ConfiguredLiteLLM)):
                context.metadata['responses'] = self._llm.results
                results = self._llm.results
                if results and all('input_tokens' in result for result in results):
                    context.n_input_tokens = sum(
                        result['input_tokens'] for result in results
                    )
                    context.n_cache_tokens = sum(
                        result['cache_tokens'] for result in results
                    )
                    context.n_output_tokens = sum(
                        result['output_tokens'] for result in results
                    )
                else:
                    context.n_input_tokens = None
                    context.n_cache_tokens = None
                    context.n_output_tokens = None
                context.cost_usd = None
