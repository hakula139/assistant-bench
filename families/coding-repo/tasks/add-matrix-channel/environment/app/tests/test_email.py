import json
import unittest

from notify.base import Message
from notify.channels.email import EmailChannel
from notify.errors import ConfigError, DeliveryError
from tests.fakes import FakeTransport

CONFIG = {"relay_url": "https://relay.example.com/send", "to": "ops@example.com"}


class EmailChannelTest(unittest.TestCase):
    def test_send(self):
        transport = FakeTransport()
        status = EmailChannel(CONFIG, transport).send(Message("Hi", "Body"))
        self.assertEqual(status, 200)
        url, body, headers = transport.posts[0]
        self.assertEqual(url, CONFIG["relay_url"])
        self.assertEqual(json.loads(body), {"subject": "Hi", "text": "Body", "to": "ops@example.com"})
        self.assertEqual(headers["Content-Type"], "application/json")

    def test_invalid_recipient(self):
        with self.assertRaisesRegex(ConfigError, "^email: "):
            EmailChannel({**CONFIG, "to": "nobody"}, FakeTransport())

    def test_delivery_error(self):
        with self.assertRaisesRegex(DeliveryError, "^email: "):
            EmailChannel(CONFIG, FakeTransport(status=502)).send(Message("Hi", "Body"))


if __name__ == "__main__":
    unittest.main()
