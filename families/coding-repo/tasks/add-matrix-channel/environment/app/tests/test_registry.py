import unittest

from notify.channels.slack import SlackChannel
from notify.errors import ConfigError
from notify.registry import CHANNELS, get_channel


class RegistryTest(unittest.TestCase):
    def test_lookup(self):
        self.assertIs(get_channel("slack"), SlackChannel)

    def test_unknown(self):
        with self.assertRaises(ConfigError):
            get_channel("pager")

    def test_sorted(self):
        names = [channel.name for channel in CHANNELS]
        self.assertEqual(names, sorted(names))


if __name__ == "__main__":
    unittest.main()
