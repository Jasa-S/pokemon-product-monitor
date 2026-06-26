import unittest
from unittest.mock import patch

from external_stores import CrazyCardsCategoryClient


class ParserFailureTests(unittest.TestCase):
    def test_crazycards_empty_gallery_is_valid(self):
        html = '<ul data-hook="product-list"></ul>'
        client = CrazyCardsCategoryClient("crazycards-pokemon", "https://www.crazycards.eu/pokemon")
        with patch("external_stores._request", return_value=html):
            self.assertEqual(client.products(), [])

    def test_crazycards_unparseable_product_items_fail(self):
        html = '''
        <ul data-hook="product-list">
          <li data-hook="product-list-grid-item" data-slug="broken-card">
            <a href="https://www.crazycards.eu/product-page/broken-card">
              <span>No stable product name hook here</span>
            </a>
          </li>
        </ul>
        '''
        client = CrazyCardsCategoryClient("crazycards-pokemon", "https://www.crazycards.eu/pokemon")
        with patch("external_stores._request", return_value=html):
            with self.assertRaisesRegex(ValueError, "could not be parsed"):
                client.products()


if __name__ == "__main__":
    unittest.main()
