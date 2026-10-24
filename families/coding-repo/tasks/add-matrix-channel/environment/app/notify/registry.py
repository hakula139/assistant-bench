"""The registry of available channels."""

from notify.base import Channel
from notify.channels.email import EmailChannel
from notify.channels.slack import SlackChannel
from notify.errors import ConfigError

CHANNELS: tuple[type[Channel], ...] = (EmailChannel, SlackChannel)


def get_channel(name: str) -> type[Channel]:
    """Return the channel class registered under ``name``."""
    for channel in CHANNELS:
        if channel.name == name:
            return channel
    raise ConfigError(f"{name}: unknown channel")
