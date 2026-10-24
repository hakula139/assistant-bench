"""Systems under test, resolved against the model catalog exported from nixos-config."""

import difflib
import hashlib
import json
import os
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from assistant_bench.generation import Generation, load_generation
from assistant_bench.messages import code, error_lines, sentence
from assistant_bench.paths import REPO_ROOT


EXPORT_VAR = 'BENCH_NIXOS_CONFIG'
NIXOS_CONFIG_PROMPT = 'nixos-config:'
SYSTEMS_FILE = 'systems.toml'
Api = Literal['openai-chat-completions', 'openai-responses']


# ── nixos-config Export ──


class ChannelCost(BaseModel):
    input: float
    output: float
    cache_read: float = Field(alias='cacheRead')


class Thinking(BaseModel):
    efforts: list[str]
    api_efforts: dict[str, list[str]] = Field(alias='apiEfforts', default={})
    default_level: str = Field(alias='defaultLevel')
    requires_effort: bool = Field(alias='requiresEffort')


class CatalogModel(BaseModel):
    """One model of nixos-config's `data/llm-models.nix`, with the fields used here."""

    name: str
    vendor: str | None = Field(default=None, min_length=1)
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
    """Rendered global instructions keyed by client, such as `claudeCode`."""


def load_export(
    env: Callable[[str], str | None] = os.environ.get,
) -> NixosConfigExport:
    """Read the export `env` names in `BENCH_NIXOS_CONFIG`.

    Raises
    ------
    RuntimeError
        The variable is unset.
    """
    path = env(EXPORT_VAR)
    if not path:
        raise RuntimeError(
            f'`{EXPORT_VAR}` is unset. Run `bench` inside `nix develop`.'
        )
    return NixosConfigExport.model_validate_json(Path(path).read_bytes())


# ── Systems ──


class Price(BaseModel):
    """Token prices in USD per million tokens."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    input: float
    output: float
    cache_read: float

    def cost(self, input_tokens: int, cached_tokens: int, output_tokens: int) -> float:
        """Return the USD cost of one call. `input_tokens` includes the cached ones."""
        return (
            (input_tokens - cached_tokens) * self.input
            + cached_tokens * self.cache_read
            + output_tokens * self.output
        ) / 1_000_000


class SystemConfig(BaseModel):
    """One table of `systems.toml`, naming either a catalog model or a bare route."""

    model_config = ConfigDict(extra='forbid')

    model: str | None = None
    route: str | None = None
    api: Api = 'openai-chat-completions'
    vendor: str | None = Field(default=None, min_length=1)
    price: Price | None = None
    max_output_tokens: int | None = Field(default=None, gt=0)
    context_window: int | None = Field(default=None, gt=0)
    effort: str | None = None
    prompt: str | None = None
    """`nixos-config:<client>` selects global instructions as the system message."""

    display_name: str | None = Field(default=None, min_length=1)
    """Overrides the catalog model's name. Required for a bare route."""

    @model_validator(mode='after')
    def _check_source(self) -> Self:
        if (self.model is None) == (self.route is None):
            raise ValueError('set exactly one of `model` and `route`')
        if self.model is not None and self.price is not None:
            raise ValueError('`price` applies only to a `route`')
        if self.model is not None and self.vendor is not None:
            raise ValueError('`vendor` applies only to a `route`')
        limits = (self.max_output_tokens, self.context_window)
        if self.model is not None and any(value is not None for value in limits):
            raise ValueError('capacity settings apply only to a `route`')
        if self.route is not None and self.display_name is None:
            raise ValueError('a `route` needs a `display_name`')
        if self.route is not None and any(value is None for value in limits):
            raise ValueError('a `route` needs `max_output_tokens` and `context_window`')
        return self


_CONFIGS = TypeAdapter(dict[str, SystemConfig], config=ConfigDict(title='systems.toml'))


class System(BaseModel):
    """The resolved generation contract and public labels for a system."""

    model_config = ConfigDict(frozen=True)

    name: str
    """Table name used by data and URLs, excluded from the fingerprint."""

    route: str
    api: Api = 'openai-chat-completions'
    max_output_tokens: int = Field(gt=0)
    """Configured completion allowance, including reasoning tokens."""

    context_window: int = Field(gt=0)
    """Configured context capacity for the agent's native context management."""

    price: Price | None
    effort: str | None
    prompt: str | None
    harness: str = 'generate'
    generation: Generation = Field(default_factory=load_generation)
    """Captured revision metadata, unchanged when another runtime resolves systems."""

    display_name: str
    """Public label, excluded from the fingerprint."""

    vendor: str | None = None

    @model_validator(mode='after')
    def _check_capacity(self) -> Self:
        if self.max_output_tokens >= self.context_window:
            raise ValueError('`max_output_tokens` must be less than `context_window`')
        return self

    @property
    def fingerprint(self) -> str:
        """Hash of what shapes the outputs. Votes pool only within one fingerprint."""
        prompt = self.prompt
        identity = {
            'version': self.generation.version(self.harness),
            'api': self.api,
            'route': self.route,
            'effort': self.effort,
            'harness': self.harness,
            'max_output_tokens': self.max_output_tokens,
            'context_window': self.context_window,
            'prompt': prompt and hashlib.sha256(prompt.encode()).hexdigest(),
        }

        encoded = json.dumps(
            {key: value for key, value in identity.items() if value is not None},
            sort_keys=True,
        )

        return hashlib.sha256(encoded.encode()).hexdigest()[:16]


def load_systems(
    export: NixosConfigExport, path: Path = REPO_ROOT / SYSTEMS_FILE
) -> dict[str, System]:
    return parse_systems(export, path.read_text())


def parse_systems(
    export: NixosConfigExport,
    document: str,
    generation: Generation | None = None,
) -> dict[str, System]:
    """Resolve every table of a `systems.toml` document.

    Raises
    ------
    ValueError
        The document does not parse or validate, a table fails to resolve, or
        two systems share a display name.
    """
    try:
        configs = _CONFIGS.validate_python(tomllib.loads(document))
    except tomllib.TOMLDecodeError as error:
        raise ValueError(
            sentence(f'`{SYSTEMS_FILE}` is not valid TOML: {error}')
        ) from error
    except ValidationError as error:
        raise ValueError(error_lines(error, f'`{SYSTEMS_FILE}`')) from error

    systems = {
        name: resolve_system(name, config, export, generation)
        for name, config in configs.items()
    }

    owners: dict[str, str] = {}
    for name, system in systems.items():
        owner = owners.setdefault(system.display_name, name)
        if owner != name:
            raise ValueError(
                f'Systems `{owner}` and `{name}` share the display name '
                f'`{system.display_name}`. Set a distinct `display_name`.'
            )

    return systems


def resolve_system(
    name: str,
    config: SystemConfig,
    export: NixosConfigExport,
    generation: Generation | None = None,
) -> System:
    """Resolve a system, defaulting effort from the catalog where it is required.

    Raises
    ------
    ValueError
        The model, its channel, the effort, or the prompt is unknown or ambiguous.
    """
    prompt = _prompt(name, config.prompt, export)
    generation = generation or load_generation()
    if config.route is not None:
        assert config.display_name is not None
        assert config.max_output_tokens is not None
        assert config.context_window is not None
        return System(
            name=name,
            generation=generation,
            route=config.route,
            api=config.api,
            max_output_tokens=config.max_output_tokens,
            context_window=config.context_window,
            vendor=config.vendor,
            price=config.price,
            effort=config.effort,
            prompt=prompt,
            display_name=config.display_name,
        )

    assert config.model is not None
    entry = export.catalog.models.get(config.model)
    if entry is None:
        close = difflib.get_close_matches(config.model, export.catalog.models)
        hint = f' Check it against {code(close)}.' if close else ''
        raise ValueError(
            f'System `{name}`: model `{config.model}` is not in the nixos-config '
            f'catalog, `data/llm-models.nix`.{hint}'
        )

    if not entry.channel_id:
        raise ValueError(
            f'System `{name}`: catalog model `{config.model}` has no channel.'
        )
    if len(entry.channel_id) > 1:
        raise ValueError(
            f'System `{name}`: catalog model `{config.model}` has channels '
            f'{code(entry.channel_id)}. Set its `route` and `price` instead.'
        )
    ((channel, route),) = entry.channel_id.items()
    return System(
        name=name,
        generation=generation,
        route=route,
        api=config.api,
        max_output_tokens=entry.max_output_tokens,
        context_window=entry.context_window,
        vendor=entry.vendor,
        price=_price(entry.channel_cost.get(channel)),
        effort=_resolve_effort(name, config.effort, entry.thinking, config.api),
        prompt=prompt,
        display_name=config.display_name or entry.name,
    )


def _prompt(name: str, reference: str | None, export: NixosConfigExport) -> str | None:
    if reference is None:
        return None
    client = reference.removeprefix(NIXOS_CONFIG_PROMPT)
    if client == reference:
        raise ValueError(
            f'System `{name}`: prompt `{reference}` is not '
            f'`{NIXOS_CONFIG_PROMPT}<client>`.'
        )
    if client not in export.instructions:
        raise ValueError(
            f'System `{name}`: nixos-config has no `{client}` instructions. '
            f'Choose among {code(export.instructions)}.'
        )
    return export.instructions[client]


def _resolve_effort(
    name: str, effort: str | None, thinking: Thinking | None, api: Api
) -> str | None:
    if thinking is None:
        if effort is not None:
            raise ValueError(f'System `{name}`: the model takes no effort.')
        return None
    if effort is None:
        effort = thinking.default_level if thinking.requires_effort else None
    allowed = thinking.api_efforts.get(api, thinking.efforts)
    if effort is not None and effort not in allowed:
        raise ValueError(
            f'System `{name}`: effort `{effort}` is not one of '
            f'{code(allowed)} for `{api}`.'
        )
    return effort


def _price(cost: ChannelCost | None) -> Price | None:
    # The catalog prices unbilled channels at zero, which report as unpriced.
    if cost is None or not (cost.input or cost.output or cost.cache_read):
        return None
    return Price(input=cost.input, output=cost.output, cache_read=cost.cache_read)
