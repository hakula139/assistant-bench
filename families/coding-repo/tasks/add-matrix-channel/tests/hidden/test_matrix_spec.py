import json
import unittest

from notify import CHANNELS, get_channel
from notify.base import Channel, Message
from notify.channels.matrix import MatrixChannel
from notify.errors import ConfigError, DeliveryError
from tests.fakes import FakeTransport

CONFIG = {"homeserver": "https://matrix.example.org/", "room_id": "!ops:example.org", "access_token": "tok"}
URL = "https://matrix.example.org/_matrix/client/v3/rooms/!ops:example.org/send/m.room.message"


class MatrixSpecTest(unittest.TestCase):
    def test_class(self):
        self.assertTrue(issubclass(MatrixChannel, Channel))
        self.assertEqual(MatrixChannel.name, "matrix")

    def test_format(self):
        channel = MatrixChannel(CONFIG, FakeTransport())
        self.assertEqual(channel.format(Message("T", "B")),
                         json.dumps({"msgtype": "m.text", "body": "T\n\nB"}, sort_keys=True))

    def test_send(self):
        transport = FakeTransport(status=204)
        status = MatrixChannel(CONFIG, transport).send(Message("Deploy", "Done"))
        self.assertEqual(status, 204)
        self.assertEqual(len(transport.posts), 1)
        url, body, headers = transport.posts[0]
        self.assertEqual(url, URL)
        self.assertEqual(json.loads(body), {"msgtype": "m.text", "body": "Deploy\n\nDone"})
        self.assertEqual(headers.get("Authorization"), "Bearer tok")
        self.assertEqual(headers.get("Content-Type"), "application/json")

    def test_no_trailing_slash(self):
        transport = FakeTransport()
        MatrixChannel({**CONFIG, "homeserver": "https://matrix.example.org"}, transport).send(Message("a", "b"))
        self.assertEqual(transport.posts[0][0], URL)

    def test_invalid_config(self):
        cases = [
            {k: v for k, v in CONFIG.items() if k != "homeserver"},
            {**CONFIG, "homeserver": "http://matrix.example.org"},
            {**CONFIG, "homeserver": ""},
            {k: v for k, v in CONFIG.items() if k != "room_id"},
            {**CONFIG, "room_id": "ops:example.org"},
            {k: v for k, v in CONFIG.items() if k != "access_token"},
            {**CONFIG, "access_token": ""},
        ]
        for config in cases:
            with self.subTest(config=config):
                with self.assertRaises(ConfigError) as caught:
                    MatrixChannel(config, FakeTransport())
                self.assertTrue(str(caught.exception).startswith("matrix: "), str(caught.exception))

    def test_delivery_error(self):
        with self.assertRaises(DeliveryError) as caught:
            MatrixChannel(CONFIG, FakeTransport(status=500)).send(Message("a", "b"))
        self.assertTrue(str(caught.exception).startswith("matrix: "))

    def test_registered_in_order(self):
        self.assertIs(get_channel("matrix"), MatrixChannel)
        self.assertEqual([channel.name for channel in CHANNELS], ["email", "matrix", "slack"])
