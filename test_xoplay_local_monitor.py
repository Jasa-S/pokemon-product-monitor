import unittest

from xoplay_local_monitor import (
    deduplicate_products,
    high_resolution_naver_image,
    normalize_raw_product,
    newer_dashboard_state,
    product_events,
    scan_events,
    wait_for_access,
)


class XoplayLocalMonitorTests(unittest.TestCase):
    def test_newer_shared_dashboard_state_supports_computer_handoff(self):
        local = {"updatedAt": "2026-01-01T10:00:00+00:00", "products": [{"key": "old"}]}
        remote = {"updatedAt": "2026-01-01T11:00:00+00:00", "products": [{"key": "new"}]}
        result = newer_dashboard_state(local, remote, {"category-id"})
        self.assertEqual(result["products"], [{"key": "new"}])
        self.assertEqual(result["categories"], ["category-id"])

    def test_upgrades_naver_thumbnail_image(self):
        thumbnail = "https://shop-phinf.pstatic.net/image.png?type=f80_80"
        self.assertEqual(
            high_resolution_naver_image(thumbnail),
            "https://shop-phinf.pstatic.net/image.png?type=f750_750",
        )

    def test_deduplicates_overlapping_naver_categories(self):
        old = {"key": "naver-pokemon:1", "productName": "Old"}
        updated = {"key": "naver-pokemon:1", "productName": "Updated"}
        other = {"key": "naver-pokemon:2", "productName": "Other"}
        self.assertEqual(deduplicate_products([old, updated, other]), [updated, other])

    def test_new_category_scope_is_silently_baselined(self):
        previous = {
            "naver-pokemon:1": {"key": "naver-pokemon:1", "isSoldOut": False}
        }
        added = {"key": "naver-pokemon:2", "isSoldOut": False}
        self.assertEqual(
            scan_events(previous, [*previous.values(), added], {"old"}, {"old", "new"}),
            [],
        )

    def test_pending_login_can_be_stopped(self):
        class Body:
            def inner_text(self):
                return "NAVER login"

        class Page:
            url = "https://nid.naver.com/nidlogin.login"

            def locator(self, _selector):
                return Body()

        self.assertFalse(wait_for_access(Page(), lambda: True))

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

    def test_normalizes_exact_naver_pokemon_category_product(self):
        product = normalize_raw_product({
            "url": "https://brand.naver.com/pokemon/products/456?query=x",
            "text": "포켓몬 카드\n20,000원", "name": "포켓몬 카드",
        }, source="naver-pokemon", slug="pokemon")
        self.assertEqual(product["key"], "naver-pokemon:456")
        self.assertEqual(product["url"], "https://brand.naver.com/pokemon/products/456")

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
