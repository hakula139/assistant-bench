"""Validate reported token counters before they enter trial accounting."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields
from datetime import datetime
from typing import Annotated, Any, Self, assert_never

from harbor.models.agent.context import AgentContext
from harbor.models.metric import UsageInfo
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from assistant_bench.catalog import Api, Price


_count = TypeAdapter(Annotated[int, Field(strict=True, ge=0)])
_reported = TypeAdapter(dict[str, Any])


@dataclass(frozen=True)
class MeasuredUsage:
    input_tokens: int
    cache_tokens: int
    cache_write_tokens: int
    output_tokens: int
    reasoning_tokens: int | None
    cache_write_unpriced: bool = False

    @classmethod
    def from_details(cls, details: Mapping[str, Any]) -> Self | None:
        """Return the details' usage, `None` when they hold no valid usage."""
        if 'input_tokens' not in details:
            return None
        return cls(**{field.name: details[field.name] for field in fields(cls)})

    def cost(self, price: Price) -> float | None:
        """Return the USD cost at the price, `None` if a cache write has no price."""
        if self.cache_write_unpriced:
            return None
        return price.cost(
            input_tokens=self.input_tokens,
            cached_tokens=self.cache_tokens,
            output_tokens=self.output_tokens,
            cache_write_tokens=self.cache_write_tokens,
        )

    @property
    def details(self) -> dict[str, Any]:
        return {
            'input_tokens': self.input_tokens,
            'cache_tokens': self.cache_tokens,
            'cache_write_tokens': self.cache_write_tokens,
            'output_tokens': self.output_tokens,
            'reasoning_tokens': self.reasoning_tokens,
            'cache_write_unpriced': self.cache_write_unpriced,
        }

    @property
    def harbor(self) -> UsageInfo:
        return UsageInfo(
            prompt_tokens=self.input_tokens,
            cache_tokens=self.cache_tokens,
            completion_tokens=self.output_tokens,
            cost_usd=0,
        )

    def record(self, context: AgentContext) -> None:
        """Set the trial's token counts, which Harbor reports and collection prices."""
        context.n_input_tokens = self.input_tokens
        context.n_cache_tokens = self.cache_tokens
        context.n_output_tokens = self.output_tokens


def combined_usage(responses: Sequence[MeasuredUsage | None]) -> MeasuredUsage | None:
    """Sum measured requests only when every admitted request reports valid usage."""
    if not responses or any(usage is None for usage in responses):
        return None
    measured = [usage for usage in responses if usage is not None]
    reasoning = [usage.reasoning_tokens for usage in measured]
    return MeasuredUsage(
        input_tokens=sum(usage.input_tokens for usage in measured),
        cache_tokens=sum(usage.cache_tokens for usage in measured),
        cache_write_tokens=sum(usage.cache_write_tokens for usage in measured),
        output_tokens=sum(usage.output_tokens for usage in measured),
        reasoning_tokens=(
            sum(value for value in reasoning if value is not None)
            if all(value is not None for value in reasoning)
            else None
        ),
        cache_write_unpriced=any(usage.cache_write_unpriced for usage in measured),
    )


def merge_messages_usage(
    current: Mapping[str, Any], delta: Mapping[str, Any]
) -> dict[str, Any]:
    """Merge partial input counters and require the delta's output count."""
    merged = dict(current) | {
        key: value for key, value in delta.items() if value is not None
    }
    merged['output_tokens'] = delta.get('output_tokens')
    return merged


def measure(api: Api, reported: object) -> dict[str, Any]:
    """Return the details a call records about its usage.

    The details hold the payload as `reported_usage`, whether it was reported but
    inconsistent, and the measured counters, which `MeasuredUsage.from_details`
    reads back.
    """
    payload = _payload(reported)
    usage = measured_usage(api, payload)
    return {
        **(usage.details if usage else {'reasoning_tokens': None}),
        'reported_usage': payload,
        'usage_invalid': payload is not None and usage is None,
    }


def record_calls(context: AgentContext, calls: list[dict[str, Any]]) -> None:
    """Record inclusive trial usage only when every completed call has valid usage."""
    context.metadata = {
        **(context.metadata or {}),
        'calls': calls,
        'cache_write_unpriced': any(
            call.get('cache_write_unpriced', False) for call in calls
        ),
    }
    if calls and all('input_tokens' in call for call in calls):
        context.n_input_tokens = sum(call['input_tokens'] for call in calls)
        context.n_cache_tokens = sum(call['cache_tokens'] for call in calls)
        context.n_output_tokens = sum(call['output_tokens'] for call in calls)
        context.metadata['cache_write_tokens'] = sum(
            call['cache_write_tokens'] for call in calls
        )
        reasoning = [call['reasoning_tokens'] for call in calls]
        context.metadata['reasoning_tokens'] = (
            None if None in reasoning else sum(reasoning)
        )
    else:
        context.n_input_tokens = None
        context.n_cache_tokens = None
        context.n_output_tokens = None
    context.cost_usd = None


def measured_usage(api: Api, reported: object) -> MeasuredUsage | None:
    """Return inclusive counters only when every reported count is consistent."""
    try:
        reported = _reported.validate_python(_payload(reported), strict=True)
        write_value: object
        cache_write_unpriced = False
        match api:
            case 'openai-chat-completions':
                input_value = reported.get('prompt_tokens')
                output_value = reported.get('completion_tokens')
                read_value = _detail(
                    reported, 'prompt_tokens_details', 'cached_tokens', 0
                )
                reasoning_value = _detail(
                    reported, 'completion_tokens_details', 'reasoning_tokens'
                )
                write_value = _detail(
                    reported, 'prompt_tokens_details', 'cache_write_tokens', 0
                )
            case 'openai-responses':
                input_value = reported.get('input_tokens')
                output_value = reported.get('output_tokens')
                read_value = _detail(
                    reported, 'input_tokens_details', 'cached_tokens', 0
                )
                reasoning_value = _detail(
                    reported, 'output_tokens_details', 'reasoning_tokens'
                )
                write_value = _detail(
                    reported, 'input_tokens_details', 'cache_write_tokens', 0
                )
            case 'anthropic-messages':
                input_value = reported.get('input_tokens')
                output_value = reported.get('output_tokens')
                read_value = reported.get('cache_read_input_tokens')
                write_value = reported.get('cache_creation_input_tokens')
                read_value = read_value if read_value is not None else 0
                write_value = write_value if write_value is not None else 0
                reasoning_value = _detail(
                    reported, 'output_tokens_details', 'thinking_tokens'
                )
            case _:
                assert_never(api)

        inputs = _count.validate_python(input_value)
        reads = _count.validate_python(read_value)
        writes = _count.validate_python(write_value)
        outputs = _count.validate_python(output_value)
        reasoning = (
            _count.validate_python(reasoning_value)
            if reasoning_value is not None
            else None
        )
        if api == 'anthropic-messages':
            inputs += reads + writes
            if reported.get('cache_creation') is not None:
                creation = _reported.validate_python(
                    reported['cache_creation'], strict=True
                )
                ttl_counts = [
                    _count.validate_python(creation.get(key))
                    for key in (
                        'ephemeral_5m_input_tokens',
                        'ephemeral_1h_input_tokens',
                    )
                ]
                if sum(ttl_counts) != writes:
                    return None
                cache_write_unpriced = ttl_counts[1] > 0
        if reads + writes > inputs or (reasoning is not None and reasoning > outputs):
            return None
        if (
            api != 'anthropic-messages'
            and 'total_tokens' in reported
            and _count.validate_python(reported['total_tokens']) != inputs + outputs
        ):
            return None
        return MeasuredUsage(
            inputs, reads, writes, outputs, reasoning, cache_write_unpriced
        )
    except ValidationError:
        return None


class Attempt(BaseModel):
    """One agent attempt and the measured usage of each response it received."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    trial: str
    system: str
    fingerprint: str
    started_at: datetime
    finished_at: datetime
    responses: list[MeasuredUsage | None]
    """`None` for a response without valid usage."""


def _payload(reported: object) -> object:
    return (
        reported.model_dump(mode='json', exclude_unset=True)
        if isinstance(reported, BaseModel)
        else reported
    )


def _detail(
    reported: Mapping[str, Any], group: str, name: str, default: int | None = None
) -> Any:
    details = reported.get(group)
    if details is None:
        return default
    details = _reported.validate_python(details, strict=True)
    value = details.get(name)
    return value if value is not None else default
