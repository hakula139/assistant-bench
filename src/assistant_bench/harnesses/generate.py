"""Harbor agent that answers a task with one model request from the host."""

import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, assert_never, override

from harbor.agents.base import BaseAgent
from harbor.agents.options import AgentOptions
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from assistant_bench.catalog import load_export
from assistant_bench.generation import load_generation
from assistant_bench.harnesses.endpoint import Endpoint, preflight
from assistant_bench.harnesses.labels import Labels
from assistant_bench.harnesses.messages import MessageResult, request_messages
from assistant_bench.harnesses.options import ApiOptions
from assistant_bench.harnesses.responses import ResponseResult
from assistant_bench.harnesses.usage import MeasuredUsage, append_attempt, measure


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
        return load_generation().version('generate', self.options.api)

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
        assert isinstance(self.options, GenerateOptions)
        started_at = datetime.now(UTC)
        try:
            await self._generate(instruction, context)
        finally:
            append_attempt(
                self.logs_dir, self.options, started_at, [context.metadata or {}]
            )

    async def _generate(self, instruction: str, context: AgentContext) -> None:
        assert isinstance(self.options, GenerateOptions)
        nixos_config_rev = load_export(self._get_env).rev
        endpoint = Endpoint.read(self._get_env, self.options.api)

        if self.options.api == 'anthropic-messages':
            try:
                await self._native_messages(endpoint, instruction, context)
            finally:
                context.metadata = (context.metadata or {}) | {
                    'nixos_config_rev': nixos_config_rev
                }
            return

        async with endpoint.client() as client:
            started = time.monotonic()
            try:
                match self.options.api:
                    case 'openai-chat-completions':
                        context.metadata = await self._chat(
                            client, instruction, context
                        )
                    case 'openai-responses':
                        context.metadata = await self._responses(
                            client, instruction, context
                        )
                    case _:
                        assert_never(self.options.api)
            finally:
                context.metadata = (context.metadata or {}) | {
                    'latency_sec': round(time.monotonic() - started, 3),
                    'nixos_config_rev': nixos_config_rev,
                }

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
            usage = MeasuredUsage.from_details(context.metadata)
            if result.complete and usage is not None:
                usage.record(context)

    async def _chat(
        self, client: AsyncOpenAI, instruction: str, context: AgentContext
    ) -> dict[str, Any]:
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
        choice = completion.choices[0]
        (self.logs_dir / OUTPUT_FILE).write_text(choice.message.content or '')

        details = measure('openai-chat-completions', completion.usage)
        usage = MeasuredUsage.from_details(details)
        if usage is not None:
            usage.record(context)

        return {
            **details,
            'responded': True,
            'finish_reason': 'refusal'
            if choice.message.refusal
            else choice.finish_reason,
            'refusal': choice.message.refusal,
            'response_model': completion.model,
            'request_id': raw.headers.get('x-request-id'),
        }

    async def _responses(
        self, client: AsyncOpenAI, instruction: str, context: AgentContext
    ) -> dict[str, Any]:
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
        usage = MeasuredUsage.from_details(details)
        if usage is not None:
            usage.record(context)

        context.metadata = details
        result.check(complete=False)
        return details

    def _messages(self, instruction: str) -> list[ChatCompletionMessageParam]:
        assert isinstance(self.options, GenerateOptions)
        user: ChatCompletionMessageParam = {'role': 'user', 'content': instruction}
        if self.options.system_prompt is None:
            return [user]
        return [{'role': 'system', 'content': self.options.system_prompt}, user]
