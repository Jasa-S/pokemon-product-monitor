import os
import tempfile
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from daily_report import build_report, live_products, should_send
from monitor import State


def product(product_id="1", source="crazycards-pokemon"):
    return {
        "key": f"{source}:{product_id}",
        "source": source,
        "productNo": product_id,
        "productName": "Pikachu Booster Pack",
        "salePrice": 9.99,
        "currency": "EUR",
        "image": None,
        "isSoldOut": False,
        "saleStatusType": "SALE",
        "stockStatus": "AVAILABLE",
        "url": "https://example.com/product",
    }


class DailyReportTests(unittest.TestCase):
    def test_report_window_allows_github_delay(self):
        berlin = ZoneInfo("Europe/Berlin")
        self.assertFalse(should_send(datetime(2026, 1, 1, 17, 59, tzinfo=berlin), False))
        self.assertTrue(should_send(datetime(2026, 1, 1, 18, 0, tzinfo=berlin), False))
        self.assertTrue(should_send(datetime(2026, 1, 1, 20, 59, tzinfo=berlin), False))
        self.assertFalse(should_send(datetime(2026, 1, 1, 21, 0, tzinfo=berlin), False))
        self.assertTrue(should_send(datetime(2026, 1, 1, 10, 0, tzinfo=berlin), True))

    def test_report_filters_retired_products(self):
        with tempfile.TemporaryDirectory() as directory:
            state = State(os.path.join(directory, "state.db"))
            state.put(product("1", "crazycards-pokemon"))
            state.put(product("old", "pokemonstore"))
            self.assertEqual([item["source"] for item in live_products(state)], ["crazycards-pokemon"])
            report = build_report(state, datetime(2026, 1, 1, 18, 0, tzinfo=ZoneInfo("Europe/Berlin")))
            self.assertEqual(report["embeds"][0]["fields"][0]["value"], "1")


if __name__ == "__main__":
    unittest.main()
