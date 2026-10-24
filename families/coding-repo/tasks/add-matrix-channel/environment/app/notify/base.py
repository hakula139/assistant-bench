"""Channel base class and the transport protocol."""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from typing import ClassVar, Protocol


@dataclass(frozen=True)
class Message:
    title: str
    body: str


class Transport(Protocol):
    def post(self, url: str, body: str, headers: Mapping[str, str]) -> int:
        """Send ``body`` to ``url`` and return the HTTP status code."""
        ...


class Channel(ABC):
    """A notification destination. Subclasses validate their configuration on creation."""

    name: ClassVar[str]

    def __init__(self, config: Mapping[str, object], transport: Transport) -> None:
        self.config = dict(config)
        self.transport = transport
        self.validate()

    @abstractmethod
    def validate(self) -> None:
        """Raise ``ConfigError`` when the configuration is unusable."""

    @abstractmethod
    def format(self, message: Message) -> str:
        """Return the request body for ``message``."""

    @abstractmethod
    def send(self, message: Message) -> int:
        """Deliver ``message`` and return the transport status code."""
