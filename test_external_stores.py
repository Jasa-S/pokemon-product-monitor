import unittest
from unittest.mock import patch

from external_stores import CrazyCardsCategoryClient, WooCommerceCategoryClient


class ExternalStoreTests(unittest.TestCase):
    def test_woocommerce_normalizes_euro_price_and_stock(self):
        payload = [{
            "id": 42, "name": "Korean Booster", "permalink": "https://shop.test/p/42",
            "prices": {"price": "4999", "currency_minor_unit": 2},
            "images": [{"src": "https://shop.test/42.jpg"}], "is_in_stock": False,
        }]
        client = WooCommerceCategoryClient("spielwaren-pokemon-kor", 208)
        with patch("external_stores._request", return_value=payload):
            product = client.products()[0]
        self.assertEqual(product["salePrice"], 49.99)
        self.assertEqual(product["currency"], "EUR")
        self.assertTrue(product["isSoldOut"])

    def test_crazycards_reads_server_rendered_product_card(self):
        html = '''
        <section data-hook="product-list"><li data-hook="product-list-grid-item">
        <div data-slug="korean-box" data-hook="product-item-root">
        <a href="https://www.crazycards.eu/product-page/korean-box">
        <img src="https://img.test/box.jpg" alt="Korean Box"></a>
        <p data-hook="product-item-name">Korean Box</p>
        <span data-wix-price="49,95 €">49,95 €</span>
        <button aria-label="Ausverkauft" disabled>Ausverkauft</button></div></li></section>
        '''
        client = CrazyCardsCategoryClient("crazycards-onepiece", "https://example.test")
        with patch("external_stores._request", return_value=html):
            product = client.products()[0]
        self.assertEqual(product["productNo"], "korean-box")
        self.assertEqual(product["salePrice"], 49.95)
        self.assertTrue(product["isSoldOut"])


if __name__ == "__main__":
    unittest.main()
