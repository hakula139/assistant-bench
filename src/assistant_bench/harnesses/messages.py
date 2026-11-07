"""Native Messages streaming, accounting, and Harbor conversation replay."""

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any, cast, override

from anthropic import AsyncAnthropic, BadRequestError, omit
from anthropic.types import Message, MessageParam, OutputConfigParam
from harbor.llms.base import ContextLengthExceededError
from pydantic import TypeAdapter

from assistant_bench.generation import load_generation
from assistant_bench.harnesses.accounting import REQUEST_ID, current_trial
from assistant_bench.harnesses.endpoint import receipt
from assistant_bench.harnesses.native import (
    ITEMS_KEY,
    NativeCompletionError,
    NativeLengthExceededError,
    NativeLLM,
    NativeSettings,
)
from assistant_bench.harnesses.usage import measure


_input = TypeAdapter(list[MessageParam])
_effort = TypeAdapter(OutputConfigParam)


class MessageStreamError(ValueError):
    """A native stream that ended without its required terminal event."""


@dataclass
class MessageResult:
    message: Message | None = None
    request_id: str | None = None
    complete: bool = False
    final_usage: bool = False
    """Whether `message_delta` delivered the final usage, which a stream broken
    before it lacks."""

    latency_sec: float | None = None

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
    def finish_reason(self) -> str | None:
        message = self.message
        if not self.complete or message is None:
            return None
        if any(
            block.type not in ('text', 'thinking', 'redacted_thinking')
            for block in message.content
        ):
            return 'unexpected_content'
        stop = message.stop_reason
        return {'end_turn': 'stop', 'max_tokens': 'length', 'refusal': 'refusal'}.get(
            stop or '', stop
        )

    @property
    def details(self) -> dict[str, Any]:
        message = self.message
        return {
            'responded': message is not None,
            'finish_reason': self.finish_reason,
            'native_stop_reason': message.stop_reason if message else None,
            'stop_details': message.stop_details.model_dump(
                mode='json', include={'type', 'category', 'explanation'}
            )
            if message and message.stop_details
            else None,
            'response_status': 'completed' if self.complete else 'incomplete',
            'response_model': message.model if message else None,
            'response_id': message.id if message else None,
            'request_id': self.request_id,
            'latency_sec': self.latency_sec,
            'anthropic_sdk_version': metadata.version('anthropic'),
            'anthropic_api_version': load_generation().anthropic_api_version,
            **measure(
                'anthropic-messages',
                message.usage if message and self.final_usage else None,
            ),
        }

    def check(self, *, complete: bool) -> None:
        if not self.complete:
            raise MessageStreamError('The Messages API ended before `message_stop`.')
        if complete:
            finish = self.finish_reason
            if finish == 'length':
                raise NativeLengthExceededError(
                    'The Messages API exhausted its completion allowance.', self
                )
            if finish != 'stop':
                raise NativeCompletionError(
                    'The Messages API did not return a complete answer.'
                )


async def request_messages(
    client: AsyncAnthropic,
    result: MessageResult,
    *,
    model: str,
    messages: list[MessageParam],
    system: str | None,
    max_tokens: int,
    effort: str | None,
    artifact: Path,
) -> None:
    """Persist any received message snapshot when streaming ends or fails."""
    started = time.monotonic()
    stopped = False
    streaming = False
    recorder = current_trial()
    try:
        async with client.messages.stream(
            model=model,
            messages=messages,
            system=system if system is not None else omit,
            max_tokens=max_tokens,
            thinking={'type': 'adaptive'},
            output_config=_effort.validate_python({'effort': effort})
            if effort
            else omit,
        ) as stream:
            streaming = True
            result.request_id = stream.request_id
            async for event in stream:
                if event.type == 'message_start':
                    result.message = event.message
                if result.message is not None:
                    result.message = stream.current_message_snapshot
                if event.type == 'message_delta':
                    result.final_usage = True
                if event.type == 'message_stop':
                    await recorder.confirm(
                        stream.response.request.extensions[REQUEST_ID],
                        receipt(
                            'anthropic-messages',
                            {
                                'type': 'message',
                                'id': result.message.id,
                                'model': result.message.model,
                                'stop_reason': result.message.stop_reason,
                                'usage': result.message.usage.model_dump(mode='json')
                                if result.final_usage
                                else None,
                            }
                            if result.message is not None
                            else None,
                            stream.response.status_code,
                            stream.request_id,
                        ),
                    )
                    stopped = True
            if stopped:
                result.message = await stream.get_final_message()
        result.complete = stopped
        result.check(complete=False)
    except BaseException:
        if streaming and not stopped:
            await recorder.uncertain()
        raise
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


class MessagesLLM(NativeLLM):
    def __init__(self, client: AsyncAnthropic, settings: NativeSettings) -> None:
        super().__init__(settings)
        self.client = client

    @override
    async def _request(
        self,
        prompt: str,
        history: Sequence[Mapping[str, Any]],
        attempt: dict[str, Any],
        **kwargs: Any,
    ) -> MessageResult:
        result = MessageResult()
        try:
            await request_messages(
                self.client,
                result,
                model=self.settings.model_name,
                messages=message_input(history, prompt),
                system=None,
                max_tokens=self.settings.output_limit,
                effort=self.settings.effort,
                artifact=self.settings.logs_dir / f'message-{len(self.attempts)}.json',
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
        return result
