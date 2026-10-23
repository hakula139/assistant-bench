"""Harbor agent that answers a task with one chat-completions call from the host."""

import time
from collections.abc import Mapping
from importlib import metadata
from typing import Any, override

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from openai.types.chat import ChatCompletionMessageParam

from assistant_bench.catalog import load_export
from assistant_bench.harnesses.endpoint import Endpoint, preflight
from assistant_bench.harnesses.labels import Labels


OUTPUT_FILE = 'output.txt'


class GenerateOptions(Labels):
    """The agent's `kwargs`, adding the request settings to the trial labels."""

    effort: str | None = None
    system_prompt: str | None = None
    max_output_tokens: int


class Generate(BaseAgent):
    """Agent that answers with one chat-completions request, writing `output.txt`.

    The model name is the endpoint's model ID. HTTP errors raise, so the job's retry
    policy reruns the trial, while a reply cut off by the token limit is a valid output.
    """

    options_model = GenerateOptions

    @staticmethod
    @override
    def name() -> str:
        return 'generate'

    @override
    def version(self) -> str:
        return metadata.version('assistant-bench')

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
    async def setup(self, environment: BaseEnvironment) -> None:
        pass

    @override
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        assert isinstance(self.options, GenerateOptions)
        assert self.model_name is not None
        nixos_config_rev = load_export().rev
        extra_body = (
            {'reasoning_effort': self.options.effort} if self.options.effort else None
        )
        endpoint = Endpoint.read(self._get_env)

        async with endpoint.client() as client:
            started = time.monotonic()
            raw = await client.chat.completions.with_raw_response.create(
                model=self.model_name,
                messages=self._messages(instruction),
                max_completion_tokens=self.options.max_output_tokens,
                extra_body=extra_body,
            )
            latency_sec = time.monotonic() - started

        completion = raw.parse()
        choice = completion.choices[0]
        (self.logs_dir / OUTPUT_FILE).write_text(choice.message.content or '')

        usage = completion.usage
        reasoning_tokens = None
        if usage is not None:
            prompt_details = usage.prompt_tokens_details
            completion_details = usage.completion_tokens_details
            context.n_input_tokens = usage.prompt_tokens
            context.n_cache_tokens = (
                prompt_details and prompt_details.cached_tokens
            ) or 0
            context.n_output_tokens = usage.completion_tokens
            reasoning_tokens = (
                completion_details and completion_details.reasoning_tokens
            )
        context.metadata = {
            'reasoning_tokens': reasoning_tokens,
            'latency_sec': round(latency_sec, 3),
            'finish_reason': choice.finish_reason,
            'response_model': completion.model,
            'request_id': raw.headers.get('x-request-id'),
            'nixos_config_rev': nixos_config_rev,
        }

    def _messages(self, instruction: str) -> list[ChatCompletionMessageParam]:
        assert isinstance(self.options, GenerateOptions)
        user: ChatCompletionMessageParam = {'role': 'user', 'content': instruction}
        if self.options.system_prompt is None:
            return [user]
        return [{'role': 'system', 'content': self.options.system_prompt}, user]
