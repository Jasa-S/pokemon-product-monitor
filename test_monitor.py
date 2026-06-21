import json
import os
import tempfile
import unittest

from monitor import (
    NaverBrandCategoryClient,
    State,
    is_available,
    keyword_match,
    normalize_image,
    normalized_product,
    product_url,
)


def sample_product(product_id=123, available=True):
    return normalized_product(
        source="test", product_id=product_id, name="Pikachu 피카츄 Plush", price=1000,
        image="//example.com/a.png", available=available,
        status="ONSALE" if available else "SOLD_OUT", url="https://example.com/product",
    )


class MonitorTests(unittest.TestCase):
    def test_availability(self):
        self.assertTrue(is_available(sample_product(available=True)))
        self.assertFalse(is_available(sample_product(available=False)))

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

    def test_naver_preloaded_state(self):
        state = {"categoryProducts": {"simpleProducts": [], "sort": None}}
        html = f'<script>window.__PRELOADED_STATE__= {json.dumps(state)}</script>'
        parsed = NaverBrandCategoryClient._preloaded_state(html)
        self.assertEqual(parsed, state)

    def test_urls(self):
        self.assertEqual(normalize_image("//example.com/a.png"), "https://example.com/a.png")
        self.assertIn("productNo=42", product_url(42))


if __name__ == "__main__":
    unittest.main()
