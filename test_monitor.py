import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from export_dashboard import dashboard_updated_at, previous_rate
from manage_watchlist import update_watchlist

from monitor import (
    NaverBrandCategoryClient,
    NaverShoppingSearchClient,
    NAVER_POKEMON_CARD_QUERIES,
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
        unknown = normalized_product(
            source="naver-pokemon", product_id=1, name="Card", price=1000,
            image=None, available=True, status="SEARCH_RESULT",
            stock_status="UNKNOWN", url="https://example.com/unknown",
        )
        self.assertFalse(is_available(unknown))

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

    def test_catalog_explicitly_includes_sold_out_products(self):
        client = PokemonStoreClient("test")
        with patch.object(
            client, "_get", return_value={"pageCount": 1, "items": []}
        ) as get:
            self.assertEqual(client.catalog(), [])
        get.assert_called_once_with(
            "/products/search",
            {
                "pageNumber": 1, "pageSize": 100, "filter.soldout": "true",
                "categoryNos": 488339,
            },
        )

    def test_new_arrivals_are_newest_card_category_products(self):
        client = PokemonStoreClient("test")
        with patch.object(client, "_get", return_value={"items": []}) as get:
            self.assertEqual(client.new_arrivals(), [])
        get.assert_called_once_with("/products/search", {
            "pageNumber": 1, "pageSize": 20, "filter.soldout": "true",
            "categoryNos": 488339, "order.by": "RECENT_PRODUCT",
        })

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

    def test_state_scope_migration_and_authoritative_retention(self):
        with tempfile.TemporaryDirectory() as directory:
            state = State(os.path.join(directory, "state.db"))
            card = sample_product(1, source="pokemonstore")
            removed = sample_product(2, source="pokemonstore")
            naver = sample_product(3, source="naver-pokemon")
            for product in (card, removed, naver):
                state.put(product)
            state.clear_source_once("naver-pokemon", "scope:cards")
            self.assertNotIn(naver, state.all())
            state.retain_source_products("pokemonstore", {card["key"]})
            self.assertEqual(state.all(), [card])
            state.put(naver)
            state.clear_source_once("naver-pokemon", "scope:cards")
            self.assertIn(naver, state.all())

    def test_external_check_interval_is_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            state = State(os.path.join(directory, "state.db"))
            self.assertTrue(state.check_due("external", 600, now=1000))
            state.mark_checked("external", checked_at=1000)
            self.assertFalse(state.check_due("external", 600, now=1599))
            self.assertTrue(state.check_due("external", 600, now=1600))

    def test_naver_preloaded_state(self):
        state = {"categoryProducts": {"simpleProducts": [], "sort": None}}
        html = f'<script>window.__PRELOADED_STATE__= {json.dumps(state)}</script>'
        parsed = NaverBrandCategoryClient._preloaded_state(html)
        self.assertEqual(parsed, state)

    def test_naver_newest_uses_whole_store_category(self):
        client = NaverBrandCategoryClient("pokemon")
        product = {
            "id": 1, "name": "Pikachu", "dispSalePrice": 1000,
            "representativeImageUrl": "https://example.com/p.png",
            "productStatusType": "SALE", "channelProductDisplayStatusType": "ON",
            "displayable": True, "stockQuantity": 1,
        }
        html = '<script>window.__PRELOADED_STATE__=' + json.dumps({
            "categoryProducts": {"simpleProducts": [product]}
        }) + '</script>'
        with patch("monitor.request_text", return_value=html) as fetch:
            products = client.newest()
        self.assertEqual([item["productNo"] for item in products], ["1"])
        self.assertIn("c94139abcef14362997090c5da975e28", fetch.call_args.args[0])

    def test_official_naver_search_accepts_brand_store_links(self):
        client = NaverShoppingSearchClient(
            "id", "secret", "pokemon", ("포켓몬센터",),
            hosts=("brand.naver.com",),
        )
        result = {"items": [{
            "link": "https://brand.naver.com/pokemon/products/123",
            "title": "<b>피카츄</b>", "lprice": "1000", "image": "https://example.com/p.png",
        }]}
        with patch("monitor.request_json", return_value=result):
            products = client.products()
        self.assertEqual(products[0]["productNo"], "123")
        self.assertEqual(products[0]["productName"], "피카츄")
        self.assertEqual(products[0]["stockStatus"], "UNKNOWN")

    def test_naver_search_can_require_card_title_terms(self):
        client = NaverShoppingSearchClient(
            "id", "secret", "pokemon", ("포켓몬",),
            hosts=("brand.naver.com",), required_title_terms=("카드",),
        )
        result = {"items": [
            {"link": "https://brand.naver.com/pokemon/products/1", "title": "피카츄 인형"},
            {"link": "https://brand.naver.com/pokemon/products/2", "title": "피카츄 카드"},
        ]}
        with patch("monitor.request_json", return_value=result):
            products = client.products()
        self.assertEqual([product["productNo"] for product in products], ["2"])

    def test_default_naver_pokemon_queries_are_card_focused(self):
        self.assertEqual(len(NAVER_POKEMON_CARD_QUERIES), 12)
        self.assertTrue(all("카드" in query for query in NAVER_POKEMON_CARD_QUERIES))

    def test_official_naver_search_accepts_matching_mall_name(self):
        client = NaverShoppingSearchClient(
            "id", "secret", "xoplay", ("포켓몬 카드",),
            mall_names=("XOPLAY", "엑스오플레이"),
        )
        result = {"items": [{
            "link": "https://search.shopping.naver.com/catalog/456",
            "productId": "456", "mallName": "XOPLAY",
            "title": "포켓몬 카드", "lprice": "2000", "image": "https://example.com/c.png",
        }]}
        with patch("monitor.request_json", return_value=result):
            products = client.products()
        self.assertEqual(products[0]["productNo"], "456")
        self.assertEqual(products[0]["source"], "naver-xoplay")

    def test_naver_search_rejects_similar_store_slug(self):
        client = NaverShoppingSearchClient(
            "id", "secret", "pokemon", ("포켓몬",),
            hosts=("smartstore.naver.com",),
        )
        result = {"items": [{
            "link": "https://smartstore.naver.com/pokemon-card-shop/products/456",
            "productId": "456", "mallName": "Unrelated seller",
            "title": "포켓몬 카드", "lprice": "2000",
        }]}
        with patch("monitor.request_json", return_value=result):
            self.assertEqual(client.products(), [])

    def test_new_products_alert_only_after_feed_baseline(self):
        config = SimpleNamespace(keywords=(), notify_on_first_run=False, webhook_url="test")
        with tempfile.TemporaryDirectory() as directory, patch("monitor.send_discord") as send:
            state = State(os.path.join(directory, "state.db"))
            observe_products(config, state, [sample_product(1)], feed="arrivals")
            send.assert_not_called()
            observe_products(config, state, [sample_product(1), sample_product(2)], feed="arrivals")
            send.assert_called_once()
            self.assertEqual(send.call_args.args[1], "✨ New product")

    def test_discovery_feed_does_not_overwrite_authoritative_state(self):
        config = SimpleNamespace(keywords=(), notify_on_first_run=False, webhook_url="")
        with tempfile.TemporaryDirectory() as directory:
            state = State(os.path.join(directory, "state.db"))
            authoritative = sample_product(1, available=False)
            state.put(authoritative)
            observe_products(
                config, state, [sample_product(1, available=True)], feed="search",
                reliable_stock=False, update_existing=False,
            )
            self.assertEqual(state.get(authoritative["key"]), authoritative)

    def test_urls(self):
        self.assertEqual(normalize_image("//example.com/a.png"), "https://example.com/a.png")
        self.assertIn("productNo=42", product_url(42))

    def test_previous_exchange_rate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "status.json")
            with open(path, "w", encoding="utf-8") as output:
                json.dump({"exchangeRate": {"rate": 0.00057}}, output)
            self.assertEqual(previous_rate(path), {"rate": 0.00057})

    def test_dashboard_timestamp_stays_stable_without_changes(self):
        content = {"products": [], "watchProductNos": [], "exchangeRate": None}
        previous = {"updatedAt": "old", **content}
        self.assertEqual(dashboard_updated_at(previous, content, "new"), "old")
        self.assertEqual(
            dashboard_updated_at(previous, {**content, "products": [{}]}, "new"), "new"
        )

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
