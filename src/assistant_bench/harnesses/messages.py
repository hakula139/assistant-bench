"""Native Messages streaming, accounting, and Harbor conversation replay."""

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any, cast, override

from anthropic import AsyncAnthropic, BadRequestError, omit
from anthropic.types import (
    Message,
    MessageParam,
    OutputConfigParam,
    ThinkingConfigParam,
)
from harbor.llms.base import (
    BaseLLM,
    ContextLengthExceededError,
    LLMResponse,
    OutputLengthExceededError,
)
from harbor.llms.chat import Chat
from pydantic import TypeAdapter

from assistant_bench.catalog import ThinkingMode
from assistant_bench.generation import load_generation
from assistant_bench.harnesses.usage import MeasuredUsage, measured_usage, usage_payload


ITEMS_KEY = 'message_content'
_input = TypeAdapter(list[MessageParam])
_effort = TypeAdapter(OutputConfigParam)
_THINKING: dict[ThinkingMode, ThinkingConfigParam] = {
    'anthropic-adaptive': {'type': 'adaptive'}
}


class MessageCompletionError(ValueError):
    """A native semantic failure that must not trigger regeneration for quality."""


class MessageStreamError(ValueError):
    """A native stream that ended without its required terminal event."""


@dataclass
class MessageResult:
    message: Message | None = None
    request_id: str | None = None
    complete: bool = False
    latency_sec: float | None = None

    @property
    def usage(self) -> MeasuredUsage | None:
        return measured_usage(
            'anthropic-messages', getattr(self.message, 'usage', None)
        )

    @property
    def text(self) -> str:
        return (
            ''.join(
                block.text for block in self.message.content if block.type == 'text'
            )
            if self.message
            else ''
        )

    @property
    def items(self) -> list[dict[str, Any]]:
        return (
            [
                block.model_dump(mode='json', exclude_unset=True)
                for block in self.message.content
            ]
            if self.message
            else []
        )

    @property
    def details(self) -> dict[str, Any]:
        message = self.message
        stop = message.stop_reason if message else None
        finish = {'end_turn': 'stop', 'max_tokens': 'length', 'refusal': 'refusal'}.get(
            stop or '', stop
        )
        if message and any(
            block.type not in ('text', 'thinking', 'redacted_thinking')
            for block in message.content
        ):
            finish = 'unexpected_content'
        usage = self.usage
        reported = usage_payload(getattr(message, 'usage', None))
        details: dict[str, Any] = {
            'finish_reason': finish if self.complete else None,
            'native_stop_reason': stop,
            'response_status': 'completed' if self.complete else 'incomplete',
            'response_model': message.model if message else None,
            'response_id': message.id if message else None,
            'request_id': self.request_id,
            'latency_sec': self.latency_sec,
            'anthropic_sdk_version': metadata.version('anthropic'),
            'anthropic_api_version': load_generation().anthropic_api_version,
            'native_usage': reported,
            'reasoning_tokens': usage.reasoning_tokens if usage else None,
            'usage_invalid': reported is not None and usage is None,
        }
        if usage is not None:
            details |= usage.details
        return details

    def check(self, *, complete: bool) -> None:
        if not self.complete:
            raise MessageStreamError('The Messages API ended before `message_stop`.')
        if complete:
            if self.details['finish_reason'] == 'length':
                raise MessageLengthExceededError(self)
            if self.details['finish_reason'] != 'stop':
                raise MessageCompletionError(
                    'The Messages API did not return a complete answer.'
                )


class MessageLengthExceededError(OutputLengthExceededError):
    """An exhausted native allowance with its replayable partial assistant content."""

    def __init__(self, result: MessageResult) -> None:
        super().__init__(
            'The Messages API exhausted its completion allowance.',
            truncated_response=result.text,
        )
        self.result = result


async def request_messages(
    client: AsyncAnthropic,
    result: MessageResult,
    *,
    model: str,
    messages: list[MessageParam],
    system: str | None,
    max_tokens: int,
    thinking_mode: ThinkingMode,
    effort: str | None,
    artifact: Path,
) -> None:
    """Accumulate typed events and retain private snapshots on every exit path."""
    started = time.monotonic()
    stopped = False
    try:
        async with client.messages.stream(
            model=model,
            messages=messages,
            system=system if system is not None else omit,
            max_tokens=max_tokens,
            thinking=_THINKING[thinking_mode],
            output_config=_effort.validate_python({'effort': effort})
            if effort
            else omit,
        ) as stream:
            result.request_id = stream.request_id
            try:
                async for event in stream:
                    if event.type == 'message_start':
                        result.message = event.message
                    if result.message is not None:
                        result.message = stream.current_message_snapshot
                    if event.type == 'message_stop':
                        stopped = True
                if stopped:
                    result.message = await stream.get_final_message()
            finally:
                if result.message is not None:
                    result.message = stream.current_message_snapshot
        result.complete = stopped
        result.check(complete=False)
    finally:
        result.latency_sec = round(time.monotonic() - started, 3)
        if result.message is not None:
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text(result.message.model_dump_json(indent=2))


def message_input(
    history: Sequence[Mapping[str, Any]], prompt: str
) -> list[MessageParam]:
    return _input.validate_python(
        [
            {'role': item['role'], 'content': item.get(ITEMS_KEY, item['content'])}
            for item in history
        ]
        + [{'role': 'user', 'content': prompt}]
    )


def attach_content(message: dict[str, Any], response: LLMResponse) -> None:
    if response.extra is not None and ITEMS_KEY in response.extra:
        message[ITEMS_KEY] = response.extra[ITEMS_KEY]


class MessagesChat(Chat):
    _truncated: tuple[str, MessageResult] | None = None

    @override
    async def chat(
        self, prompt: str, logging_path: Path | None = None, **kwargs: Any
    ) -> LLMResponse:
        self._truncated = None
        try:
            response = await super().chat(prompt, logging_path, **kwargs)
        except MessageLengthExceededError as error:
            self._truncated = (prompt, error.result)
            raise
        attach_content(self.messages[-1], response)
        return response

    @override
    def reset_response_chain(self) -> None:
        super().reset_response_chain()
        if self._truncated is not None:
            prompt, result = self._truncated
            if (
                len(self.messages) >= 2
                and self.messages[-2]['content'] == prompt
                and self.messages[-1]['content'] == result.text
            ):
                self.messages[-1][ITEMS_KEY] = result.items
            self._truncated = None


class MessagesLLM(BaseLLM):
    def __init__(
        self,
        client: AsyncAnthropic,
        model_name: str,
        context_limit: int,
        output_limit: int,
        effort: str | None,
        thinking_mode: ThinkingMode,
        logs_dir: Path,
    ) -> None:
        self.client = client
        self.model_name = model_name.removeprefix('openai/')
        self.context_limit = context_limit
        self.output_limit = output_limit
        self.effort = effort
        self.thinking_mode: ThinkingMode = thinking_mode
        self.logs_dir = logs_dir
        self.results: list[dict[str, Any]] = []
        self.attempts: list[dict[str, Any]] = []

    @override
    def get_model_context_limit(self) -> int:
        return self.context_limit

    @override
    def get_model_output_limit(self) -> int:
        return self.output_limit

    @override
    async def call(
        self,
        prompt: str,
        message_history: list[dict[str, Any]] | None = None,
        logging_path: Path | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        result = MessageResult()
        attempt: dict[str, Any] = {}
        self.attempts.append(attempt)
        ordinal = len(self.attempts)
        try:
            await request_messages(
                self.client,
                result,
                model=self.model_name,
                messages=message_input(message_history or [], prompt),
                system=None,
                max_tokens=self.output_limit,
                thinking_mode=self.thinking_mode,
                effort=self.effort,
                artifact=self.logs_dir / f'message-{ordinal}.json',
            )
        except BadRequestError as error:
            body = error.body
            raw_detail = (
                cast(Mapping[str, object], body).get('error')
                if isinstance(body, Mapping)
                else None
            )
            detail: Mapping[str, object] = (
                cast(Mapping[str, object], raw_detail)
                if isinstance(raw_detail, Mapping)
                else {}
            )
            if (
                detail.get('type') == 'invalid_request_error'
                and isinstance(message := detail.get('message'), str)
                and message.startswith('prompt is too long')
            ):
                raise ContextLengthExceededError(message) from error
            raise
        finally:
            attempt.update(result.details)
            if result.complete:
                self.results.append(result.details)
        result.check(complete=True)
        details = result.details
        return LLMResponse(
            content=result.text,
            model_name=details['response_model'],
            response_id=details['response_id'],
            usage=result.usage.harbor if result.usage else None,
            extra=details | {ITEMS_KEY: result.items},
        )
