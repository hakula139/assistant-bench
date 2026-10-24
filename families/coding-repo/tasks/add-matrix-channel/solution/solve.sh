#!/usr/bin/env bash

set -euo pipefail

cd /app
cat >notify/channels/matrix.py <<'PY'
"""Matrix rooms through the client-server API."""

import json
import logging

from notify.base import Channel, Message
from notify.errors import ConfigError, DeliveryError

logger = logging.getLogger(__name__)


class MatrixChannel(Channel):
    name = "matrix"

    def validate(self) -> None:
        homeserver = self.config.get("homeserver")
        room_id = self.config.get("room_id")
        token = self.config.get("access_token")
        if not isinstance(homeserver, str) or not homeserver.startswith("https://"):
            raise ConfigError("matrix: homeserver must be an https:// URL")
        if not isinstance(room_id, str) or not room_id.startswith("!"):
            raise ConfigError("matrix: room_id must start with !")
        if not isinstance(token, str) or not token:
            raise ConfigError("matrix: access_token is required")

    def format(self, message: Message) -> str:
        return json.dumps({"msgtype": "m.text", "body": f"{message.title}\n\n{message.body}"}, sort_keys=True)

    def send(self, message: Message) -> int:
        homeserver = str(self.config["homeserver"]).rstrip("/")
        url = f"{homeserver}/_matrix/client/v3/rooms/{self.config['room_id']}/send/m.room.message"
        headers = {"Authorization": f"Bearer {self.config['access_token']}", "Content-Type": "application/json"}
        status = self.transport.post(url, self.format(message), headers)
        if not 200 <= status < 300:
            raise DeliveryError(f"matrix: delivery failed with status {status}")
        logger.info("matrix: delivered %r", message.title)
        return status
PY
cat >tests/test_matrix.py <<'PY'
import json
import unittest

from notify.base import Message
from notify.channels.matrix import MatrixChannel
from notify.errors import ConfigError, DeliveryError
from tests.fakes import FakeTransport

CONFIG = {"homeserver": "https://m.example.org/", "room_id": "!r:example.org", "access_token": "t"}


class MatrixChannelTest(unittest.TestCase):
    def test_send(self):
        transport = FakeTransport()
        MatrixChannel(CONFIG, transport).send(Message("T", "B"))
        url, body, headers = transport.posts[0]
        self.assertEqual(url, "https://m.example.org/_matrix/client/v3/rooms/!r:example.org/send/m.room.message")
        self.assertEqual(json.loads(body), {"msgtype": "m.text", "body": "T\n\nB"})
        self.assertEqual(headers["Authorization"], "Bearer t")

    def test_invalid_room(self):
        with self.assertRaisesRegex(ConfigError, "^matrix: "):
            MatrixChannel({**CONFIG, "room_id": "r"}, FakeTransport())

    def test_delivery_error(self):
        with self.assertRaisesRegex(DeliveryError, "^matrix: "):
            MatrixChannel(CONFIG, FakeTransport(status=500)).send(Message("T", "B"))
PY
python3 - <<'PY'
import pathlib

registry = pathlib.Path("notify/registry.py")
text = registry.read_text()
text = text.replace("from notify.channels.email import EmailChannel\n",
                    "from notify.channels.email import EmailChannel\nfrom notify.channels.matrix import MatrixChannel\n")
text = text.replace("(EmailChannel, SlackChannel)", "(EmailChannel, MatrixChannel, SlackChannel)")
registry.write_text(text)

changelog = pathlib.Path("CHANGELOG.md")
changelog.write_text(changelog.read_text().replace("- Add the `slack` channel.\n",
                                                   "- Add the `slack` channel.\n- Add the `matrix` channel.\n"))
PY
