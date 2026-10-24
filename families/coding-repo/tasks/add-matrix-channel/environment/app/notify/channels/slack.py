"""Slack incoming webhooks."""

import json
import logging

from notify.base import Channel, Message
from notify.errors import ConfigError, DeliveryError

logger = logging.getLogger(__name__)


class SlackChannel(Channel):
    name = "slack"

    def validate(self) -> None:
        url = self.config.get("webhook_url")
        if not isinstance(url, str) or not url:
            raise ConfigError("slack: webhook_url is required")
        if not url.startswith("https://hooks.slack.com/"):
            raise ConfigError("slack: webhook_url must be a Slack webhook URL")

    def format(self, message: Message) -> str:
        return json.dumps({"text": f"*{message.title}*\n{message.body}"}, sort_keys=True)

    def send(self, message: Message) -> int:
        status = self.transport.post(str(self.config["webhook_url"]), self.format(message),
                                     {"Content-Type": "application/json"})
        if not 200 <= status < 300:
            raise DeliveryError(f"slack: delivery failed with status {status}")
        logger.info("slack: delivered %r", message.title)
        return status
