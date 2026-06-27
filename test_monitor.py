import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from export_dashboard import dashboard_product, source_summaries
from external_stores import CrazyCardsCategoryClient, WooCommerceCategoryClient, requested_category_clients
from monitor import LIVE_SOURCES, State, checked_products, is_available, keyword_match, observe_products


def sample_product(product_id="123", available=True, source="crazycards-pokemon"):
    return {
        "key": f"{source}:{product_id}",
        "source": source,
        "productNo": str(product_id),
        "productName": "Pikachu Booster Pack",
        "salePrice": 9.99,
        "currency": "EUR",
        "image": "https://example.com/a.png",
        "isSoldOut": not available,
        "saleStatusType": "SALE" if available else "OUTOFSTOCK",
        "stockStatus": "AVAILABLE" if available else "SOLD_OUT",
        "url": "https://example.com/product",
    }


class MonitorTests(unittest.TestCase):
    def test_live_sources_are_only_eu_card_shops(self):
        self.assertEqual(
            LIVE_SOURCES,
            {
                "crazycards-onepiece",
                "crazycards-pokemon",
                "spielwaren-onepiece-kor",
                "spielwaren-pokemon-kor",
            },
        )

    def test_requested_clients_cover_live_sources(self):
        self.assertEqual({client.source for client in requested_category_clients()}, LIVE_SOURCES)

    def test_availability(self):
        self.assertTrue(is_available(sample_product(available=True)))
        self.assertFalse(is_available(sample_product(available=False)))

    def test_keyword_filter_is_case_insensitive(self):
        product = sample_product()
        self.assertTrue(keyword_match(product, ("pikachu",)))
        self.assertFalse(keyword_match(product, ("eevee",)))

    def test_state_round_trip_and_check_interval(self):
        with tempfile.TemporaryDirectory() as directory:
            state = State(os.path.join(directory, "state.db"))
            product = sample_product()
            self.assertIsNone(state.get(product["key"]))
            state.put(product)
            self.assertEqual(state.get(product["key"]), product)
            self.assertEqual(len(state.all()), 1)
            self.assertEqual(state.source_product_count("crazycards-pokemon"), 1)
            self.assertTrue(state.check_due("external:crazycards-pokemon", 300, now=1000))
            state.mark_checked("external:crazycards-pokemon", checked_at=1000)
            self.assertFalse(state.check_due("external:crazycards-pokemon", 300, now=1299))
            self.assertTrue(state.check_due("external:crazycards-pokemon", 300, now=1300))

    def test_zero_product_scan_is_valid(self):
        with tempfile.TemporaryDirectory() as directory:
            state = State(os.path.join(directory, "state.db"))
            state.put(sample_product("1"))
            self.assertEqual(checked_products("crazycards-pokemon", [], state), [])

    def test_store_error_state_only_alerts_on_transition(self):
        with tempfile.TemporaryDirectory() as directory:
            state = State(os.path.join(directory, "state.db"))
            self.assertTrue(state.mark_feed_error("external:crazycards-pokemon", "boom"))
            self.assertFalse(state.mark_feed_error("external:crazycards-pokemon", "still boom"))
            self.assertTrue(state.clear_feed_error("external:crazycards-pokemon"))
            self.assertFalse(state.clear_feed_error("external:crazycards-pokemon"))

    def test_new_products_alert_only_after_feed_baseline(self):
        config = SimpleNamespace(keywords=(), notify_on_first_run=False, webhook_url="test")
        with tempfile.TemporaryDirectory() as directory, patch("monitor.send_discord") as send:
            state = State(os.path.join(directory, "state.db"))
            observe_products(config, state, [sample_product("1")], feed="external:crazycards-pokemon")
            send.assert_not_called()
            observe_products(
                config,
                state,
                [sample_product("1"), sample_product("2")],
                feed="external:crazycards-pokemon",
            )
            send.assert_called_once()
            self.assertEqual(send.call_args.args[1], "✨ New product")

    def test_restock_alert(self):
        config = SimpleNamespace(keywords=(), notify_on_first_run=False, webhook_url="test")
        with tempfile.TemporaryDirectory() as directory, patch("monitor.send_discord") as send:
            state = State(os.path.join(directory, "state.db"))
            state.mark_feed_initialized("external:crazycards-pokemon")
            state.put(sample_product("1", available=False))
            observe_products(config, state, [sample_product("1", available=True)], feed="external:crazycards-pokemon")
            send.assert_called_once()
            self.assertEqual(send.call_args.args[1], "✅ Back in stock")

    def test_dashboard_exports_only_live_sources(self):
        self.assertTrue(dashboard_product(sample_product(source="crazycards-pokemon")))
        self.assertFalse(dashboard_product(sample_product(source="pokemonstore")))
        self.assertFalse(dashboard_product(sample_product(source="naver-xoplay")))

    def test_source_summaries_include_health(self):
        with tempfile.TemporaryDirectory() as directory:
            state = State(os.path.join(directory, "state.db"))
            product = sample_product("1")
            state.put(product)
            state.mark_checked("external:crazycards-pokemon", checked_at=1000)
            summary = {item["source"]: item for item in source_summaries(state, [product])}["crazycards-pokemon"]
            self.assertEqual(summary["productCount"], 1)
            self.assertEqual(summary["availableCount"], 1)
            self.assertEqual(summary["checkedAt"], "1970-01-01T00:16:40+00:00")
            self.assertFalse(summary["failing"])

    def test_woocommerce_normalization_and_pagination(self):
        first_page = [
            {
                "id": number,
                "name": f"Booster {number}",
                "prices": {"currency_minor_unit": 2, "price": "1299"},
                "images": [{"src": "https://example.com/p.png"}],
                "is_in_stock": True,
                "permalink": f"https://spielwarenparadies24.de/p/booster-{number}",
            }
            for number in range(100)
        ]
        second_page = [{
            "id": 101,
            "name": "Final Booster",
            "prices": {"currency_minor_unit": 2, "price": "999"},
            "images": [],
            "is_in_stock": False,
            "permalink": "https://spielwarenparadies24.de/p/final-booster",
        }]
        client = WooCommerceCategoryClient("spielwaren-pokemon-kor", 208)
        with patch("external_stores._request", side_effect=[first_page, second_page]) as fetch:
            products = client.products()
        self.assertEqual(len(products), 101)
        self.assertIn("page=2", fetch.call_args_list[1].args[0])
        self.assertEqual(products[0]["salePrice"], 12.99)
        self.assertEqual(products[-1]["stockStatus"], "SOLD_OUT")

    def test_crazycards_parser(self):
        html = '''
        <ul data-hook="product-list">
          <li data-hook="product-list-grid-item" data-slug="pikachu-card">
            <a href="https://www.crazycards.eu/product-page/pikachu-card">
              <img src="https://static.wixstatic.com/media/card.jpg/v1/fill/w_100,h_100/card.jpg">
              <p data-hook="product-item-name">Pikachu Card</p>
              <span data-wix-price="9,99 €"></span>
            </a>
          </li>
          <li class="x" data-hook="product-list-grid-item" data-slug="sold-out-card">
            <a href="https://www.crazycards.eu/product-page/sold-out-card" aria-label="Sold out">
              <p data-hook="product-item-name">Sold Out Card</p>
              <span data-wix-price="0,00 €"></span>
            </a>
          </li>
        </ul>
        '''
        client = CrazyCardsCategoryClient("crazycards-pokemon", "https://www.crazycards.eu/pokemon")
        with patch("external_stores._request", return_value=html):
            products = client.products()
        self.assertEqual(products[0]["productNo"], "pikachu-card")
        self.assertEqual(products[0]["salePrice"], 9.99)
        self.assertEqual(products[0]["stockStatus"], "AVAILABLE")
        self.assertEqual(products[1]["salePrice"], None)
        self.assertEqual(products[1]["stockStatus"], "SOLD_OUT")


if __name__ == "__main__":
    unittest.main()
