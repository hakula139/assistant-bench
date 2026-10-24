"""Email delivery through an HTTP mail relay."""

import json
import logging

from notify.base import Channel, Message
from notify.errors import ConfigError, DeliveryError

logger = logging.getLogger(__name__)


class EmailChannel(Channel):
    name = "email"

    def validate(self) -> None:
        relay = self.config.get("relay_url")
        recipient = self.config.get("to")
        if not isinstance(relay, str) or not relay:
            raise ConfigError("email: relay_url is required")
        if not isinstance(recipient, str) or "@" not in recipient:
            raise ConfigError("email: to must be an email address")

    def format(self, message: Message) -> str:
        return json.dumps({"subject": message.title, "text": message.body, "to": self.config["to"]}, sort_keys=True)

    def send(self, message: Message) -> int:
        status = self.transport.post(str(self.config["relay_url"]), self.format(message),
                                     {"Content-Type": "application/json"})
        if not 200 <= status < 300:
            raise DeliveryError(f"email: delivery failed with status {status}")
        logger.info("email: delivered %r", message.title)
        return status
