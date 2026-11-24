"""Harbor agent that answers a task with one model request from the host."""

import time
from collections.abc import Mapping
from typing import Any, assert_never, override

from harbor.agents.base import BaseAgent
from harbor.agents.options import AgentOptions
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from assistant_bench.catalog import load_export
from assistant_bench.generation import load_generation
from assistant_bench.harnesses.accounting import current_trial
from assistant_bench.harnesses.endpoint import Endpoint, preflight
from assistant_bench.harnesses.messages import MessageResult, request_messages
from assistant_bench.harnesses.options import ApiOptions, Labels
from assistant_bench.harnesses.responses import ResponseResult
from assistant_bench.harnesses.usage import MeasuredUsage, measure


OUTPUT_FILE = 'output.txt'


class GenerateOptions(AgentOptions, Labels, ApiOptions):
    effort: str | None = None
    system_prompt: str | None = None
    max_output_tokens: int


class Generate(BaseAgent):
    """Answer with one stateless request, writing `output.txt`.

    HTTP errors raise for Harbor's retry policy. Incomplete replies remain recorded
    attempts and fail the completion gate.
    """

    options_model = GenerateOptions

    @staticmethod
    @override
    def name() -> str:
        return 'generate'

    @override
    def version(self) -> str:
        assert isinstance(self.options, GenerateOptions)
        return load_generation().version(False, self.options.api)

    @classmethod
    @override
    def preflight(
        cls,
        kwargs: dict[str, Any] | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        super().preflight(kwargs, env)
        preflight(env, ApiOptions.model_validate(kwargs).api)

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
        with current_trial().track():
            await self._generate(instruction, context)

    async def _generate(self, instruction: str, context: AgentContext) -> None:
        assert isinstance(self.options, GenerateOptions)
        nixos_config_rev = load_export(self._get_env).rev
        endpoint = Endpoint.read(self._get_env, self.options.api)
        started = time.monotonic()
        try:
            match self.options.api:
                case 'anthropic-messages':
                    await self._native_messages(endpoint, instruction, context)
                case 'openai-chat-completions':
                    async with endpoint.client('openai-chat-completions') as client:
                        await self._chat(client, instruction, context)
                case 'openai-responses':
                    async with endpoint.client('openai-responses') as client:
                        await self._responses(client, instruction, context)
                case _:
                    assert_never(self.options.api)
        finally:
            details = dict(context.metadata or {})
            if details.get('latency_sec') is None:
                details['latency_sec'] = round(time.monotonic() - started, 3)
            details['nixos_config_rev'] = nixos_config_rev
            context.metadata = details

    async def _native_messages(
        self, endpoint: Endpoint, instruction: str, context: AgentContext
    ) -> None:
        assert isinstance(self.options, GenerateOptions)
        assert self.model_name is not None
        result = MessageResult()
        try:
            async with endpoint.messages_client() as client:
                await request_messages(
                    client,
                    result,
                    model=self.model_name,
                    messages=[{'role': 'user', 'content': instruction}],
                    system=self.options.system_prompt,
                    max_tokens=self.options.max_output_tokens,
                    effort=self.options.effort,
                    artifact=self.logs_dir / 'message.json',
                )
            (self.logs_dir / OUTPUT_FILE).write_text(result.text)
        finally:
            context.metadata = result.details
            if result.complete:
                _record_usage(context, result.details)

    async def _chat(
        self, client: AsyncOpenAI, instruction: str, context: AgentContext
    ) -> None:
        assert isinstance(self.options, GenerateOptions)
        assert self.model_name is not None
        extra_body = (
            {'reasoning_effort': self.options.effort} if self.options.effort else None
        )

        raw = await client.chat.completions.with_raw_response.create(
            model=self.model_name,
            messages=self._messages(instruction),
            max_completion_tokens=self.options.max_output_tokens,
            extra_body=extra_body,
        )
        completion = raw.parse()
        details = {
            **measure('openai-chat-completions', completion.usage),
            'responded': True,
            'response_model': completion.model,
            'request_id': raw.headers.get('x-request-id'),
        }
        context.metadata = details
        _record_usage(context, details)

        choice = completion.choices[0]
        (self.logs_dir / OUTPUT_FILE).write_text(choice.message.content or '')
        context.metadata = {
            **details,
            'finish_reason': 'refusal'
            if choice.message.refusal
            else choice.finish_reason,
            'refusal': choice.message.refusal,
        }

    async def _responses(
        self, client: AsyncOpenAI, instruction: str, context: AgentContext
    ) -> None:
        assert isinstance(self.options, GenerateOptions)
        assert self.model_name is not None
        extra_body = (
            {'reasoning': {'effort': self.options.effort}}
            if self.options.effort
            else None
        )

        raw = await client.responses.with_raw_response.create(
            model=self.model_name,
            input=instruction,
            instructions=self.options.system_prompt,
            max_output_tokens=self.options.max_output_tokens,
            store=False,
            extra_body=extra_body,
        )
        result = ResponseResult(raw.parse(), raw.headers.get('x-request-id'))
        response = result.response
        (self.logs_dir / OUTPUT_FILE).write_text(response.output_text)

        details = result.details
        _record_usage(context, details)
        context.metadata = details
        result.check(complete=False)

    def _messages(self, instruction: str) -> list[ChatCompletionMessageParam]:
        assert isinstance(self.options, GenerateOptions)
        user: ChatCompletionMessageParam = {'role': 'user', 'content': instruction}
        if self.options.system_prompt is None:
            return [user]
        return [{'role': 'system', 'content': self.options.system_prompt}, user]


def _record_usage(context: AgentContext, details: Mapping[str, Any]) -> None:
    usage = MeasuredUsage.from_details(details)
    if usage is not None:
        usage.record(context)
