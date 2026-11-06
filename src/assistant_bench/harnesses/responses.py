"""Native Responses results and stateless conversation items."""

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, override

from harbor.llms.base import ContextLengthExceededError
from openai import AsyncOpenAI, BadRequestError
from openai.types.responses import Response, ResponseInputParam
from pydantic import TypeAdapter

from assistant_bench.harnesses.native import (
    ITEMS_KEY,
    NativeCompletionError,
    NativeLengthExceededError,
    NativeLLM,
    NativeSettings,
)
from assistant_bench.harnesses.usage import measure


_input = TypeAdapter(ResponseInputParam)


@dataclass(frozen=True)
class ResponseResult:
    response: Response
    request_id: str | None
    latency_sec: float | None = None

    @property
    def text(self) -> str:
        return self.response.output_text

    @property
    def refusals(self) -> list[str]:
        return [
            content.refusal
            for item in self.response.output
            if item.type == 'message'
            for content in item.content
            if content.type == 'refusal'
        ]

    @property
    def incomplete_reason(self) -> str | None:
        details = self.response.incomplete_details
        return details.reason if details else None

    @property
    def finish_reason(self) -> str:
        response = self.response
        incomplete = self.incomplete_reason
        return (
            'error'
            if response.error is not None
            else 'unexpected_content'
            if response.status in ('completed', 'incomplete')
            and any(item.type == 'function_call' for item in response.output)
            else 'refusal'
            if self.refusals
            else 'stop'
            if response.status == 'completed'
            else 'length'
            if incomplete == 'max_output_tokens'
            else incomplete or response.status or 'unknown'
        )

    @property
    def details(self) -> dict[str, Any]:
        response = self.response
        return {
            **measure('openai-responses', response.usage),
            'responded': True,
            'finish_reason': self.finish_reason,
            'response_status': response.status,
            'incomplete_reason': self.incomplete_reason,
            'refusals': self.refusals,
            'response_model': response.model,
            'response_id': response.id,
            'request_id': self.request_id,
            'latency_sec': self.latency_sec,
            'response_error': response.error.model_dump(exclude_unset=True)
            if response.error
            else None,
        }

    @property
    def items(self) -> list[dict[str, Any]]:
        return [
            item.model_dump(mode='json', by_alias=True, exclude_unset=True)
            for item in self.response.output
        ]

    def check(self, *, complete: bool) -> None:
        response = self.response
        if response.error is not None or response.status not in (
            'completed',
            'incomplete',
        ):
            raise NativeCompletionError(
                'The Responses API did not return a finished response.'
            )
        finish = self.finish_reason
        if finish == 'unexpected_content':
            raise NativeCompletionError(
                'The Responses API returned an unexpected function call.'
            )
        if complete:
            if finish == 'length':
                raise NativeLengthExceededError(
                    'The Responses API exhausted its completion allowance.', self
                )
            if finish != 'stop':
                raise NativeCompletionError(
                    'The Responses API did not return a complete answer.'
                )


def response_input(
    history: Sequence[Mapping[str, Any]], prompt: str
) -> ResponseInputParam:
    items: list[dict[str, Any]] = []
    for message in history:
        if ITEMS_KEY in message:
            items.extend(message[ITEMS_KEY])
        else:
            items.append({'role': message['role'], 'content': message['content']})
    items.append({'role': 'user', 'content': prompt})
    return _input.validate_python(items)


class ResponsesLLM(NativeLLM):
    def __init__(self, client: AsyncOpenAI, settings: NativeSettings) -> None:
        super().__init__(settings)
        self.client = client

    @override
    async def _request(
        self,
        prompt: str,
        history: Sequence[Mapping[str, Any]],
        attempt: dict[str, Any],
        **kwargs: Any,
    ) -> ResponseResult:
        started = time.monotonic()
        attempt['response_status'] = 'request_failed'
        try:
            raw = await self.client.responses.with_raw_response.create(
                model=self.settings.model_name,
                input=response_input(history, prompt),
                max_output_tokens=self.settings.output_limit,
                store=False,
                extra_headers=kwargs.get('extra_headers'),
                extra_body={'reasoning': {'effort': self.settings.effort}}
                if self.settings.effort
                else {},
            )
        except BadRequestError as error:
            if error.code == 'context_length_exceeded':
                raise ContextLengthExceededError(
                    'The Responses API exhausted its input context.'
                ) from error
            raise
        finally:
            attempt['latency_sec'] = round(time.monotonic() - started, 3)

        result = ResponseResult(
            raw.parse(), raw.headers.get('x-request-id'), attempt['latency_sec']
        )
        attempt.update(result.details)
        artifact = self.settings.logs_dir / f'response-{len(self.calls) + 1}.json'
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(result.response.model_dump_json(indent=2, by_alias=True))
        return result
