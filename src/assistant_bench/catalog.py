"""Systems under test, resolved against the model catalog exported from nixos-config."""

import difflib
import os
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from assistant_bench.generation import Generation, load_generation
from assistant_bench.messages import InputError, code, error_lines, validated_toml
from assistant_bench.paths import REPO_ROOT


EXPORT_VAR = 'BENCH_NIXOS_CONFIG'
NIXOS_CONFIG_PROMPT = 'nixos-config:'
SYSTEMS_FILE = 'systems.toml'
Api = Literal['openai-chat-completions', 'openai-responses', 'anthropic-messages']
ThinkingMode = Literal['anthropic-adaptive']


# ── nixos-config Export ──


class ChannelCost(BaseModel):
    input: float
    output: float
    cache_read: float = Field(alias='cacheRead')
    cache_write: float | None = Field(alias='cacheWrite', default=None)


class Thinking(BaseModel):
    mode: str | None = None
    efforts: list[str]
    default_level: str = Field(alias='defaultLevel')
    requires_effort: bool = Field(alias='requiresEffort')


class CatalogModel(BaseModel):
    """One model of nixos-config's `data/llm-models.nix`, with the fields used here."""

    apis: list[str]
    vendor: str = Field(min_length=1)
    max_output_tokens: int = Field(alias='maxTokens', gt=0)
    context_window: int = Field(alias='contextWindow', gt=0)
    channel_id: dict[str, str] = Field(alias='channelId')
    channel_cost: dict[str, ChannelCost] = Field(alias='channelCost', default={})
    thinking: Thinking | None = None


class Catalog(BaseModel):
    models: dict[str, CatalogModel]


class NixosConfigExport(BaseModel):
    """The flake's `nixos-config` package, exported to the dev shell."""

    rev: str
    catalog: Catalog
    instructions: dict[str, str]
    """Rendered global instructions keyed by client."""


def load_export(
    env: Callable[[str], str | None] = os.environ.get,
) -> NixosConfigExport:
    """Read the export `env` names in `BENCH_NIXOS_CONFIG`.

    Raises
    ------
    InputError
        The variable is unset.
    """
    path = env(EXPORT_VAR)
    if not path:
        raise InputError(f'`{EXPORT_VAR}` is unset. Run `bench` inside `nix develop`.')
    return NixosConfigExport.model_validate_json(Path(path).read_bytes())


# ── Systems ──


class Price(BaseModel):
    """Token prices in USD per million tokens."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    input: float
    output: float
    cache_read: float
    cache_write: float | None = None

    def cost(
        self,
        input_tokens: int,
        cached_tokens: int,
        output_tokens: int,
        cache_write_tokens: int = 0,
    ) -> float | None:
        """Return the USD cost of one call. `input_tokens` includes the cached ones."""
        if cache_write_tokens and self.cache_write is None:
            return None
        return (
            (input_tokens - cached_tokens - cache_write_tokens) * self.input
            + cached_tokens * self.cache_read
            + cache_write_tokens * (self.cache_write or 0)
            + output_tokens * self.output
        ) / 1_000_000


class SystemConfig(BaseModel):
    """One table of `systems.toml`."""

    model_config = ConfigDict(extra='forbid')

    model: str
    api: Api = 'openai-chat-completions'
    effort: str | None = None
    prompt: str | None = None
    """`nixos-config:<client>` selects global instructions as the system message."""

    display_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    """Public label the site and reports show."""


_CONFIGS = TypeAdapter(dict[str, SystemConfig], config=ConfigDict(title='systems.toml'))


class SystemIdentity(BaseModel):
    """The generation facts a harness builds its agent kwargs from."""

    model_config = ConfigDict(frozen=True)

    route: str
    api: Api
    max_output_tokens: int = Field(gt=0)
    """Configured completion allowance, including reasoning tokens."""

    context_window: int = Field(gt=0)
    """Configured context capacity for the agent's native context management."""

    effort: str | None
    thinking_mode: ThinkingMode | None = None
    prompt: str | None
    harness: str
    generation: Generation
    """Captured revision metadata, unchanged when another runtime resolves systems."""

    @model_validator(mode='after')
    def _check_capacity(self) -> Self:
        if self.max_output_tokens >= self.context_window:
            raise ValueError('`max_output_tokens` must be less than `context_window`')
        if self.api == 'anthropic-messages' and self.thinking_mode is None:
            raise ValueError(
                'native Messages requires the `anthropic-adaptive` thinking mode'
            )
        return self


class System(SystemIdentity):
    """An admitted runnable identity and its public labels and prices."""

    name: str
    """Table name used by data and URLs, excluded from the fingerprint."""

    display_name: str
    """Public label, excluded from the fingerprint."""

    price: Price | None
    vendor: str


def load_systems(
    export: NixosConfigExport, path: Path = REPO_ROOT / SYSTEMS_FILE
) -> dict[str, System]:
    return parse_systems(export, path.read_text(), load_generation())


def parse_systems(
    export: NixosConfigExport, document: str, generation: Generation
) -> dict[str, System]:
    """Resolve every table of a `systems.toml` document.

    Raises
    ------
    InputError
        The document does not parse or validate, a table fails to resolve, or
        two systems share a display name.
    """
    systems = {
        name: resolve_system(name, config, export, generation)
        for name, config in parse_system_configs(document).items()
    }

    owners: dict[str, str] = {}
    for name, system in systems.items():
        owner = owners.setdefault(system.display_name, name)
        if owner != name:
            raise InputError(
                f'Systems `{owner}` and `{name}` share the display name '
                f'`{system.display_name}`. Set a distinct `display_name`.'
            )

    return systems


def parse_system_configs(document: str) -> dict[str, SystemConfig]:
    return validated_toml(_CONFIGS, document, f'`{SYSTEMS_FILE}`')


def resolve_system(
    name: str,
    config: SystemConfig,
    export: NixosConfigExport,
    generation: Generation,
) -> System:
    """Resolve a system, defaulting effort from the catalog where it is required.

    Raises
    ------
    InputError
        The model, its channel, the API, the effort, or the prompt is unknown or
        ambiguous, or the model's capacities or thinking mode do not fit.
    """
    entry = _catalog_model(name, config.model, export)
    if not entry.apis:
        raise InputError(
            f'System `{name}`: the catalog model declares no supported APIs.'
        )
    if config.api not in entry.apis:
        raise InputError(
            f'System `{name}`: API `{config.api}` is not one of {code(entry.apis)}.'
        )

    thinking = entry.thinking
    effort = config.effort
    if effort is None and thinking is not None and thinking.requires_effort:
        effort = thinking.default_level
    _check_effort(name, effort, thinking)

    adaptive = thinking is not None and thinking.mode == 'anthropic-adaptive'
    ((channel, route),) = entry.channel_id.items()
    try:
        return System(
            generation=generation,
            route=route,
            api=config.api,
            max_output_tokens=entry.max_output_tokens,
            context_window=entry.context_window,
            effort=effort,
            thinking_mode=(
                'anthropic-adaptive'
                if config.api == 'anthropic-messages' and adaptive
                else None
            ),
            prompt=_prompt(name, config.prompt, export),
            harness='generate',
            name=name,
            display_name=config.display_name,
            price=_price(entry.channel_cost.get(channel)),
            vendor=entry.vendor,
        )
    except ValidationError as error:
        raise InputError(error_lines(error, f'System `{name}`')) from error


def _catalog_model(name: str, model: str, export: NixosConfigExport) -> CatalogModel:
    entry = export.catalog.models.get(model)
    if entry is None:
        close = difflib.get_close_matches(model, export.catalog.models)
        hint = f' Check it against {code(close)}.' if close else ''
        raise InputError(
            f'System `{name}`: model `{model}` is not in the nixos-config '
            f'catalog, `data/llm-models.nix`.{hint}'
        )

    if len(entry.channel_id) != 1:
        channels = (
            f'channels {code(entry.channel_id)}' if entry.channel_id else 'no channel'
        )
        raise InputError(
            f'System `{name}`: catalog model `{model}` has {channels}. Name a '
            'catalog model with one channel.'
        )
    return entry


def _prompt(name: str, reference: str | None, export: NixosConfigExport) -> str | None:
    if reference is None:
        return None
    client = reference.removeprefix(NIXOS_CONFIG_PROMPT)
    if client == reference:
        raise InputError(
            f'System `{name}`: prompt `{reference}` is not '
            f'`{NIXOS_CONFIG_PROMPT}<client>`.'
        )
    if client not in export.instructions:
        raise InputError(
            f'System `{name}`: nixos-config has no `{client}` instructions. '
            f'Choose among {code(export.instructions)}.'
        )
    return export.instructions[client]


def _check_effort(
    name: str,
    effort: str | None,
    thinking: Thinking | None,
) -> None:
    if thinking is None:
        if effort is not None:
            raise InputError(f'System `{name}`: the model takes no effort.')
        return
    if effort is not None and effort not in thinking.efforts:
        raise InputError(
            f'System `{name}`: effort `{effort}` is not one of '
            f'{code(thinking.efforts)}.'
        )


def _price(cost: ChannelCost | None) -> Price | None:
    # The catalog prices unbilled channels at zero, which report as unpriced.
    if cost is None or not (cost.input or cost.output or cost.cache_read):
        return None
    return Price(
        input=cost.input,
        output=cost.output,
        cache_read=cost.cache_read,
        cache_write=cost.cache_write,
    )
