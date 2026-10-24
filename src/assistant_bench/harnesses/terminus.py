"""Harbor's terminus-2 agent, sending its model calls to the bench endpoint."""

from collections.abc import Mapping
from importlib import metadata
from pathlib import Path
from typing import Any, ClassVar, Self, override

from harbor.agents.terminus_2.terminus_2 import Terminus2, Terminus2Options
from harbor.environments.base import BaseEnvironment
from harbor.llms.base import BaseLLM, LLMBackend, LLMResponse
from harbor.llms.lite_llm import LiteLLM
from harbor.llms.litellm_config import configure_litellm_debug
from harbor.models.agent.context import AgentContext
from litellm import Message
from pydantic import BaseModel

from assistant_bench.catalog import load_export
from assistant_bench.generation import generation_version
from assistant_bench.harnesses.endpoint import Endpoint, preflight
from assistant_bench.harnesses.labels import Labels


class TerminusOptions(Labels, Terminus2Options):
    pass


class _ConfiguredLiteLLM(LiteLLM):
    """Keep resolved capacities per instance, outside LiteLLM's global registry."""

    _context_limit: int
    _output_limit: int

    @classmethod
    def configured(
        cls, model_name: str, context_limit: int, output_limit: int, **kwargs: Any
    ) -> Self:
        llm = cls(model_name=model_name, **kwargs)
        llm._context_limit = context_limit
        llm._output_limit = output_limit
        return llm

    @override
    def get_model_context_limit(self) -> int:
        return self._context_limit

    @override
    def get_model_output_limit(self) -> int:
        return self._output_limit

    @override
    async def _call_responses(
        self,
        prompt: str,
        message_history: list[dict[str, Any] | Message] | None = None,
        response_format: dict[str, Any] | type[BaseModel] | None = None,
        logging_path: Path | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        kwargs['store'] = False
        kwargs['previous_response_id'] = None
        return await super()._call_responses(
            prompt, message_history or [], response_format, logging_path, **kwargs
        )


class Terminus(Terminus2):
    """Harbor's `Terminus2`, whose LiteLLM client comes from the bench's endpoint.

    The client carries the key file, the CA bundle, the extra headers, and the
    local address, so none of them enters the job's `kwargs`, `env`, or config.
    """

    options_model: ClassVar[type[Terminus2Options]] = TerminusOptions

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
        constructor_kwargs: dict[str, Any] = {
            **(llm_kwargs or {}),
            'client': endpoint.client(),
        }
        if temperature is not None:
            constructor_kwargs['temperature'] = temperature
        configure_litellm_debug(debug=constructor_kwargs.pop('litellm_debug', False))

        return _ConfiguredLiteLLM.configured(
            context_limit=model_info['max_input_tokens'],
            output_limit=model_info['max_output_tokens'],
            model_name=model_name,
            api_base=api_base,
            collect_rollout_details=collect_rollout_details,
            session_id=session_id,
            max_thinking_tokens=max_thinking_tokens,
            reasoning_effort=reasoning_effort,
            use_responses_api=use_responses_api,
            **constructor_kwargs,
        )

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
            context.metadata = {
                **(context.metadata or {}),
                'nixos_config_rev': nixos_config_rev,
                'harbor_version': harbor_version,
            }
