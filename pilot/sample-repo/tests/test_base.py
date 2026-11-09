import unittest

from shop.cart import item_count
from shop.formatting import currency
from shop.pricing import subtotal


class ShopTest(unittest.TestCase):
    def test_subtotal(self):
        self.assertEqual(subtotal([10, 20, 30]), 60)

    def test_item_count(self):
        self.assertEqual(item_count([{"qty": 2}, {"qty": 3}]), 2)

    def test_currency(self):
        self.assertEqual(currency(1234), "$12.34")


if __name__ == "__main__":
    unittest.main()
