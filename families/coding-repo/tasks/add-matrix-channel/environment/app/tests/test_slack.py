import json
import unittest

from notify.base import Message
from notify.channels.slack import SlackChannel
from notify.errors import ConfigError, DeliveryError
from tests.fakes import FakeTransport

CONFIG = {"webhook_url": "https://hooks.slack.com/services/T/B/X"}


class SlackChannelTest(unittest.TestCase):
    def test_send(self):
        transport = FakeTransport()
        SlackChannel(CONFIG, transport).send(Message("Deploy", "Done"))
        url, body, _ = transport.posts[0]
        self.assertEqual(url, CONFIG["webhook_url"])
        self.assertEqual(json.loads(body), {"text": "*Deploy*\nDone"})

    def test_missing_url(self):
        with self.assertRaisesRegex(ConfigError, "^slack: "):
            SlackChannel({}, FakeTransport())

    def test_delivery_error(self):
        with self.assertRaisesRegex(DeliveryError, "^slack: "):
            SlackChannel(CONFIG, FakeTransport(status=404)).send(Message("Deploy", "Done"))


if __name__ == "__main__":
    unittest.main()
