import json
import tempfile
import unittest
from pathlib import Path

from dispatchbox import FakeReceiver, dispatch_pending, place_order


class TestOrderPipeline(unittest.TestCase):
    def test_placed_order_reaches_receiver(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            order = place_order(root / 'orders.sqlite', 'example', 1200)
            receiver = FakeReceiver(root / 'receiver.sqlite')

            result = dispatch_pending(root / 'orders.sqlite', receiver)

            self.assertEqual(order.order_id, 'example')
            self.assertEqual(
                (result.attempted, result.delivered, result.failed), (1, 1, 0)
            )
            self.assertEqual(
                {
                    key: json.loads(payload)
                    for key, payload in receiver.effects().items()
                },
                {'order:example': {'order_id': 'example', 'amount_cents': 1200}},
            )


if __name__ == '__main__':
    unittest.main()
