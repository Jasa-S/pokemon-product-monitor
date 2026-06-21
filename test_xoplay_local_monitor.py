import unittest

from xoplay_local_monitor import normalize_raw_product, product_events


class XoplayLocalMonitorTests(unittest.TestCase):
    def test_normalizes_card(self):
        product = normalize_raw_product({
            "url": "https://smartstore.naver.com/xoplay/products/123",
            "text": "피카츄 카드\n12,000원\n품절",
            "name": "피카츄 카드",
            "image": "https://example.com/card.png",
        })
        self.assertEqual(product["productNo"], "123")
        self.assertEqual(product["salePrice"], 12000)
        self.assertTrue(product["isSoldOut"])

    def test_first_scan_is_silent_then_detects_new_and_restock(self):
        sold_out = normalize_raw_product({
            "url": "https://smartstore.naver.com/xoplay/products/1",
            "text": "상품\n1,000원\n품절", "name": "상품",
        })
        available = {**sold_out, "isSoldOut": False, "stockStatus": "AVAILABLE"}
        new_product = normalize_raw_product({
            "url": "https://smartstore.naver.com/xoplay/products/2",
            "text": "새 상품\n2,000원", "name": "새 상품",
        })
        self.assertEqual(product_events({}, [sold_out]), [])
        events = product_events({sold_out["key"]: sold_out}, [available, new_product])
        self.assertEqual([event for event, _product in events], ["restock", "new"])


if __name__ == "__main__":
    unittest.main()
