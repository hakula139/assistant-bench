"""Systems under test, resolved against the model catalog exported from nixos-config."""

import hashlib
import json
import os
import tomllib
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from assistant_bench.paths import REPO_ROOT


EXPORT_VAR = 'BENCH_NIXOS_CONFIG'
NIXOS_CONFIG_PROMPT = 'nixos-config:'


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


# ── nixos-config Export ──


class ChannelCost(BaseModel):
    input: float
    output: float
    cache_read: float = Field(alias='cacheRead')


class Thinking(BaseModel):
    efforts: list[str]
    default_level: str = Field(alias='defaultLevel')
    requires_effort: bool = Field(alias='requiresEffort')


class CatalogModel(BaseModel):
    """One model of nixos-config's `data/llm-models.nix`, with the fields used here."""

    channel_id: dict[str, str] = Field(alias='channelId')
    channel_cost: dict[str, ChannelCost] = Field(alias='channelCost', default={})
    thinking: Thinking | None = None


class Catalog(BaseModel):
    models: dict[str, CatalogModel]


class NixosConfigExport(BaseModel):
    """The flake's `nixos-config` package, exported to the dev shell.

    `instructions` maps each client, such as `claudeCode`, to its rendered global
    instruction file.
    """

    rev: str
    catalog: Catalog
    instructions: dict[str, str]
    prose_tics: str = Field(alias='proseTics')


def load_export() -> NixosConfigExport:
    """Read the export that the dev shell names in `BENCH_NIXOS_CONFIG`.

    Raises
    ------
    RuntimeError
        The variable is unset outside the dev shell.
    """
    path = os.environ.get(EXPORT_VAR)
    if not path:
        raise RuntimeError(f'{EXPORT_VAR} is unset. Run bench inside `nix develop`.')
    return NixosConfigExport.model_validate_json(Path(path).read_bytes())


# ── Systems ──


class SystemConfig(BaseModel):
    """One table of `systems.toml`, naming either a catalog model or a bare route.

    `prompt` is `nixos-config:<client>`, a rendered instruction file that becomes
    the system message.
    """

    model_config = ConfigDict(extra='forbid')

    model: str | None = None
    route: str | None = None
    effort: str | None = None
    price: Price | None = None
    prompt: str | None = None

    @model_validator(mode='after')
    def _check_source(self) -> Self:
        if (self.model is None) == (self.route is None):
            raise ValueError('set exactly one of `model` and `route`')
        if self.model is not None and self.price is not None:
            raise ValueError('`price` applies only to a `route`')
        return self


class System(BaseModel):
    """A system resolved to the route, effort, prompt, and price of every call."""

    model_config = ConfigDict(frozen=True)

    name: str
    route: str
    effort: str | None
    prompt: str | None
    price: Price | None
    harness: str = 'generate'

    @property
    def fingerprint(self) -> str:
        """Hash of what shapes the outputs. Votes pool only within one fingerprint."""
        prompt = self.prompt
        identity = {
            'route': self.route,
            'effort': self.effort,
            'harness': self.harness,
            'prompt': prompt and hashlib.sha256(prompt.encode()).hexdigest(),
        }
        encoded = json.dumps(
            {key: value for key, value in identity.items() if value is not None},
            sort_keys=True,
        )
        return hashlib.sha256(encoded.encode()).hexdigest()[:16]


def load_systems(
    export: NixosConfigExport, path: Path = REPO_ROOT / 'systems.toml'
) -> dict[str, System]:
    return parse_systems(export, path.read_text())


def parse_systems(export: NixosConfigExport, document: str) -> dict[str, System]:
    return {
        name: resolve_system(name, SystemConfig.model_validate(table), export)
        for name, table in tomllib.loads(document).items()
    }


def resolve_system(
    name: str, config: SystemConfig, export: NixosConfigExport
) -> System:
    """Resolve a system, defaulting effort from the catalog where it is required.

    Raises
    ------
    ValueError
        The model, its channel, the effort, or the prompt is unknown or ambiguous.
    """
    prompt = _prompt(name, config.prompt, export)
    if config.route is not None:
        return System(
            name=name,
            route=config.route,
            effort=config.effort,
            prompt=prompt,
            price=config.price,
        )

    assert config.model is not None
    entry = export.catalog.models.get(config.model)
    if entry is None:
        raise ValueError(f'system {name!r}: unknown catalog model {config.model!r}')

    if not entry.channel_id:
        raise ValueError(
            f'system {name!r}: catalog model {config.model!r} has no channel'
        )
    if len(entry.channel_id) > 1:
        channels = ', '.join(entry.channel_id)
        raise ValueError(
            f'system {name!r}: catalog model {config.model!r} has channels '
            f'{channels}, so set its `route` and `price` instead'
        )
    ((channel, route),) = entry.channel_id.items()
    return System(
        name=name,
        route=route,
        effort=_resolve_effort(name, config.effort, entry.thinking),
        prompt=prompt,
        price=_price(entry.channel_cost.get(channel)),
    )


def _prompt(name: str, reference: str | None, export: NixosConfigExport) -> str | None:
    if reference is None:
        return None
    client = reference.removeprefix(NIXOS_CONFIG_PROMPT)
    if client == reference:
        raise ValueError(
            f'system {name!r}: prompt {reference!r} is not nixos-config:<client>'
        )
    if client not in export.instructions:
        accepted = ', '.join(export.instructions)
        raise ValueError(
            f'system {name!r}: nixos-config has no {client!r} instructions '
            f'(expected one of {accepted})'
        )
    return export.instructions[client]


def _resolve_effort(
    name: str, effort: str | None, thinking: Thinking | None
) -> str | None:
    if thinking is None:
        if effort is not None:
            raise ValueError(f'system {name!r}: the model takes no effort')
        return None
    if effort is None:
        return thinking.default_level if thinking.requires_effort else None
    if effort not in thinking.efforts:
        accepted = ', '.join(thinking.efforts)
        raise ValueError(f'system {name!r}: effort {effort!r} is not one of {accepted}')
    return effort


def _price(cost: ChannelCost | None) -> Price | None:
    # The catalog prices unbilled channels at zero, which report as unpriced.
    if cost is None or not (cost.input or cost.output or cost.cache_read):
        return None
    return Price(input=cost.input, output=cost.output, cache_read=cost.cache_read)
