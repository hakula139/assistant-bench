"""Native Responses results and stateless conversation items."""

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, override

import httpx
from harbor.llms.base import (
    BaseLLM,
    ContextLengthExceededError,
    LLMResponse,
    OutputLengthExceededError,
)
from harbor.llms.chat import Chat
from openai import APIError, AsyncOpenAI, BadRequestError
from openai.types.responses import Response, ResponseInputParam
from pydantic import TypeAdapter

from assistant_bench.harnesses.usage import MeasuredUsage, measured_usage, usage_payload


ITEMS_KEY = 'response_items'
_input = TypeAdapter(ResponseInputParam)


@dataclass(frozen=True)
class ResponseResult:
    response: Response
    request_id: str | None

    @property
    def usage(self) -> MeasuredUsage | None:
        return measured_usage('openai-responses', getattr(self.response, 'usage', None))

    @property
    def details(self) -> dict[str, Any]:
        response = self.response
        usage = self.usage
        reported = usage_payload(getattr(response, 'usage', None))
        refusals = [
            content.refusal
            for item in response.output
            if item.type == 'message'
            for content in item.content
            if content.type == 'refusal'
        ]
        incomplete = (
            response.incomplete_details.reason if response.incomplete_details else None
        )
        finish = (
            'error'
            if response.error is not None
            else 'refusal'
            if refusals
            else 'stop'
            if response.status == 'completed'
            else 'length'
            if incomplete == 'max_output_tokens'
            else incomplete or response.status or 'unknown'
        )
        return {
            'reasoning_tokens': usage.reasoning_tokens if usage else None,
            'reported_usage': reported,
            'usage_invalid': reported is not None and usage is None,
            'finish_reason': finish,
            'response_status': response.status,
            'incomplete_reason': incomplete,
            'refusals': refusals,
            'response_model': response.model,
            'response_id': response.id,
            'request_id': self.request_id,
            'response_error': response.error.model_dump() if response.error else None,
        }

    @property
    def items(self) -> list[dict[str, Any]]:
        return [
            item.model_dump(mode='json', exclude_unset=True)
            for item in self.response.output
        ]

    def check(self, request: httpx.Request, *, complete: bool) -> None:
        response = self.response
        if response.error is not None or response.status not in (
            'completed',
            'incomplete',
        ):
            raise ResponseCompletionError(
                'The Responses API did not return a finished response.',
                request=request,
                body=None,
            )
        if any(item.type == 'function_call' for item in response.output):
            raise ResponseCompletionError(
                'The Responses API returned an unexpected function call.',
                request=request,
                body=None,
            )
        if complete:
            if self.details['finish_reason'] == 'length':
                error = ResponseLengthExceededError(self)
                raise error
            if self.details['finish_reason'] != 'stop':
                raise ResponseCompletionError(
                    'The Responses API did not return a complete answer.',
                    request=request,
                    body=None,
                )


class ResponseCompletionError(APIError):
    """An accepted request whose semantic result must not trigger regeneration."""


class ResponseLengthExceededError(OutputLengthExceededError):
    """A native budget failure whose items remain attached to its partial answer."""

    def __init__(self, result: ResponseResult) -> None:
        super().__init__(
            'The Responses API exhausted its completion allowance.',
            truncated_response=result.response.output_text,
        )
        self.result = result


class ResponsesChat(Chat):
    """Keep SDK output items on the native assistant message that owns them."""

    _truncated: tuple[str, ResponseResult] | None = None

    @override
    async def chat(
        self, prompt: str, logging_path: Path | None = None, **kwargs: Any
    ) -> LLMResponse:
        self._truncated = None

        try:
            response = await super().chat(prompt, logging_path, **kwargs)
        except ResponseLengthExceededError as error:
            self._truncated = (prompt, error.result)
            raise
        attach_items(self.messages[-1], response)
        return response

    @override
    def reset_response_chain(self) -> None:
        """Attach partial items after Harbor appends a budget failure outside `chat`."""
        super().reset_response_chain()
        if self._truncated is not None:
            prompt, result = self._truncated
            if (
                len(self.messages) >= 2
                and self.messages[-2]['content'] == prompt
                and self.messages[-1]['content'] == result.response.output_text
            ):
                self.messages[-1][ITEMS_KEY] = result.items
            self._truncated = None


def attach_items(message: dict[str, Any], response: LLMResponse) -> None:
    if response.extra is not None and ITEMS_KEY in response.extra:
        message[ITEMS_KEY] = response.extra[ITEMS_KEY]


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


class ResponsesLLM(BaseLLM):
    """Use SDK items while Harbor owns tool execution and conversation accounting."""

    def __init__(
        self,
        client: AsyncOpenAI,
        model_name: str,
        context_limit: int,
        output_limit: int,
        effort: str | None,
        temperature: float | None,
        logs_dir: Path,
    ) -> None:
        self.client = client
        self.model_name = model_name.removeprefix('openai/')
        self.context_limit = context_limit
        self.output_limit = output_limit
        self.effort = effort
        self.temperature = temperature
        self.logs_dir = logs_dir
        self.results: list[dict[str, Any]] = []

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
        started = time.monotonic()
        try:
            raw = await self.client.responses.with_raw_response.create(
                model=self.model_name,
                input=response_input(message_history or [], prompt),
                max_output_tokens=self.output_limit,
                store=False,
                extra_headers=kwargs.get('extra_headers'),
                extra_body=(
                    {'reasoning': {'effort': self.effort}} if self.effort else {}
                )
                | (
                    {'temperature': self.temperature}
                    if self.temperature is not None
                    else {}
                ),
            )
        except APIError as error:
            self.results.append(
                {
                    'response_status': 'request_failed',
                    'latency_sec': round(time.monotonic() - started, 3),
                }
            )
            if (
                isinstance(error, BadRequestError)
                and error.code == 'context_length_exceeded'
            ):
                raise ContextLengthExceededError(
                    'The Responses API exhausted its input context.'
                ) from error
            raise

        result = ResponseResult(raw.parse(), raw.headers.get('x-request-id'))
        details = result.details | {'latency_sec': round(time.monotonic() - started, 3)}
        usage = result.usage
        if usage is not None:
            details |= usage.details
        self.results.append(details)
        artifact = self.logs_dir / f'response-{len(self.results)}.json'
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(result.response.model_dump_json(indent=2))
        result.check(raw.http_response.request, complete=True)
        return LLMResponse(
            content=result.response.output_text,
            model_name=result.response.model,
            response_id=result.response.id,
            usage=usage.harbor if usage else None,
            extra=details | {ITEMS_KEY: result.items},
        )
