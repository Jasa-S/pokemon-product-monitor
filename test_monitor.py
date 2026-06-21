import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from export_dashboard import previous_rate
from manage_watchlist import update_watchlist

from monitor import (
    NaverBrandCategoryClient,
    PokemonStoreClient,
    State,
    is_available,
    keyword_match,
    normalize_image,
    normalized_product,
    product_url,
    observe_products,
    load_watchlist,
    translate_product_name,
)


def sample_product(product_id=123, available=True, source="test"):
    return normalized_product(
        source=source, product_id=product_id, name="Pikachu 피카츄 Plush", price=1000,
        image="//example.com/a.png", available=available,
        status="ONSALE" if available else "SOLD_OUT", url="https://example.com/product",
    )


class MonitorTests(unittest.TestCase):
    def test_availability(self):
        self.assertTrue(is_available(sample_product(available=True)))
        self.assertFalse(is_available(sample_product(available=False)))

    def test_option_stock_distinguishes_partial_and_sold_out(self):
        products = [sample_product(1, source="pokemonstore"), sample_product(2, source="pokemonstore")]
        client = PokemonStoreClient("test")
        response = {
            "optionInfos": [
                {"mallProductNo": 1, "options": [
                    {"saleType": "AVAILABLE", "children": []},
                    {"saleType": "SOLDOUT", "children": []},
                ]},
                {"mallProductNo": 2, "options": [
                    {"saleType": "SOLDOUT", "children": []},
                ]},
            ]
        }
        with patch.object(client, "_get", return_value=response):
            client._add_option_stock(products)
        self.assertEqual(products[0]["stockStatus"], "PARTIAL")
        self.assertFalse(products[0]["isSoldOut"])
        self.assertEqual(products[0]["availableOptionCount"], 1)
        self.assertEqual(products[1]["stockStatus"], "SOLD_OUT")
        self.assertTrue(products[1]["isSoldOut"])

    def test_keyword_filter_is_case_insensitive(self):
        product = sample_product()
        self.assertTrue(keyword_match(product, ("pikachu",)))
        self.assertTrue(keyword_match(product, ("피카츄",)))
        self.assertFalse(keyword_match(product, ("eevee",)))

    def test_state_round_trip_and_noop_update(self):
        with tempfile.TemporaryDirectory() as directory:
            state = State(os.path.join(directory, "state.db"))
            product = sample_product()
            self.assertIsNone(state.get(product["key"]))
            state.put(product)
            self.assertEqual(state.get(product["key"]), product)
            state.put(product)
            self.assertEqual(len(state.all()), 1)
            self.assertFalse(state.feed_initialized("sample"))
            state.mark_feed_initialized("sample")
            self.assertTrue(state.feed_initialized("sample"))

    def test_naver_preloaded_state(self):
        state = {"categoryProducts": {"simpleProducts": [], "sort": None}}
        html = f'<script>window.__PRELOADED_STATE__= {json.dumps(state)}</script>'
        parsed = NaverBrandCategoryClient._preloaded_state(html)
        self.assertEqual(parsed, state)

    def test_new_products_alert_only_after_feed_baseline(self):
        config = SimpleNamespace(keywords=(), notify_on_first_run=False, webhook_url="test")
        with tempfile.TemporaryDirectory() as directory, patch("monitor.send_discord") as send:
            state = State(os.path.join(directory, "state.db"))
            observe_products(config, state, [sample_product(1)], feed="arrivals")
            send.assert_not_called()
            observe_products(config, state, [sample_product(1), sample_product(2)], feed="arrivals")
            send.assert_called_once()
            self.assertEqual(send.call_args.args[1], "✨ New product")

    def test_urls(self):
        self.assertEqual(normalize_image("//example.com/a.png"), "https://example.com/a.png")
        self.assertIn("productNo=42", product_url(42))

    def test_previous_exchange_rate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "status.json")
            with open(path, "w", encoding="utf-8") as output:
                json.dump({"exchangeRate": {"rate": 0.00057}}, output)
            self.assertEqual(previous_rate(path), {"rate": 0.00057})

    def test_watchlist_update(self):
        with tempfile.TemporaryDirectory() as directory:
            database = os.path.join(directory, "state.db")
            watchlist = os.path.join(directory, "watchlist.json")
            state = State(database)
            state.put(sample_product(123, source="pokemonstore"))
            update_watchlist("[watch] 123", watchlist, database)
            self.assertEqual(load_watchlist(watchlist), {123})
            update_watchlist("[unwatch] 123", watchlist, database)
            self.assertEqual(load_watchlist(watchlist), set())

    def test_translation_without_token_is_optional(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(translate_product_name("피카츄 봉제인형"))


if __name__ == "__main__":
    unittest.main()
