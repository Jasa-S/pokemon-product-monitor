"""Public category clients for the requested German and EU card shops."""

from __future__ import annotations

import gzip
import json
import re
import time
from html import unescape
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


USER_AGENT = "PokemonStoreAvailabilityMonitor/3.0 (+personal-use; EU-card-shops)"


def _wix_original_image(url: str | None) -> str | None:
    """Return Wix's original media asset instead of its tiny blurred gallery thumbnail."""
    if not url:
        return None
    match = re.match(r"(https://static\.wixstatic\.com/media/[^/?]+)", unescape(url))
    return match.group(1) if match else unescape(url)


def _request(url: str, *, as_json: bool) -> Any:
    request = Request(url, headers={
        "Accept": "application/json" if as_json else "text/html,application/xhtml+xml",
        "Accept-Encoding": "gzip",
        "Accept-Language": "de-DE,de;q=0.9,en;q=0.7",
        "User-Agent": USER_AGENT,
    })
    for attempt in range(3):
        try:
            with urlopen(request, timeout=30) as response:
                body = response.read()
                if response.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
                text = body.decode("utf-8", errors="replace")
                return json.loads(text) if as_json else text
        except HTTPError as error:
            if error.code not in {429, 500, 502, 503, 504} or attempt == 2:
                raise
            time.sleep(3 * (attempt + 1))
    raise RuntimeError("external shop request retry loop ended unexpectedly")


def _product(
    *, source: str, product_id: str | int, name: str, price: float | None,
    image: str | None, available: bool, url: str,
) -> dict[str, Any]:
    return {
        "key": f"{source}:{product_id}",
        "source": source,
        "productNo": str(product_id),
        "productName": unescape(name),
        "salePrice": price,
        "currency": "EUR",
        "image": image,
        "isSoldOut": not available,
        "saleStatusType": "SALE" if available else "OUTOFSTOCK",
        "stockStatus": "AVAILABLE" if available else "SOLD_OUT",
        "url": url,
    }


class WooCommerceCategoryClient:
    API = "https://spielwarenparadies24.de/wp-json/wc/store/v1/products"
    PAGE_SIZE = 100

    def __init__(self, source: str, category_id: int) -> None:
        self.source = source
        self.category_id = category_id

    def products(self) -> list[dict[str, Any]]:
        products = []
        page = 1
        while True:
            data = _request(
                f"{self.API}?category={self.category_id}&per_page={self.PAGE_SIZE}&page={page}",
                as_json=True,
            )
            if not isinstance(data, list):
                raise ValueError(f"Unexpected WooCommerce response for {self.source}")
            for item in data:
                prices = item.get("prices") or {}
                minor_unit = int(prices.get("currency_minor_unit", 2))
                raw_price = prices.get("price")
                images = item.get("images") or []
                price = None
                if raw_price not in {None, ""}:
                    price = int(raw_price) / (10 ** minor_unit)
                products.append(_product(
                    source=self.source, product_id=item["id"], name=item["name"],
                    price=price, image=images[0].get("src") if images else None,
                    available=bool(item.get("is_in_stock")), url=item["permalink"],
                ))
            if len(data) < self.PAGE_SIZE:
                break
            page += 1
        return products


class CrazyCardsCategoryClient:
    """Parse Wix's stable server-rendered product data-hooks without running JavaScript."""

    PRODUCT_ITEM_RE = re.compile(
        r'<li\b(?=[^>]*data-hook="product-list-grid-item")[^>]*>.*?'
        r'(?=<li\b(?=[^>]*data-hook="product-list-grid-item")|</ul>|$)',
        re.S,
    )

    def __init__(self, source: str, url: str) -> None:
        self.source = source
        self.url = url

    @staticmethod
    def _parse_price(chunk: str) -> float | None:
        price_match = re.search(r'data-wix-price="([\d.,]+)[^\d"]*€"', chunk)
        if not price_match:
            return None
        price = float(price_match.group(1).replace(".", "").replace(",", "."))
        return price if price > 0 else None

    @staticmethod
    def _is_sold_out(chunk: str) -> bool:
        text = unescape(re.sub(r"<[^>]+>", " ", chunk)).casefold()
        markers = (
            'aria-label="ausverkauft"',
            'aria-label="sold out"',
            "ausverkauft",
            "sold out",
            "nicht verfügbar",
        )
        lowered = chunk.casefold()
        return any(marker in lowered or marker in text for marker in markers)

    def products(self) -> list[dict[str, Any]]:
        html = _request(self.url, as_json=False)
        chunks = self.PRODUCT_ITEM_RE.findall(html)
        products: dict[str, dict[str, Any]] = {}
        parse_failures = 0
        for chunk in chunks:
            slug_match = re.search(r'data-slug="([^"]+)"', chunk[:500])
            url_match = re.search(r'href="(https://www\.crazycards\.eu/product-page/[^"]+)"', chunk)
            name_match = re.search(
                r'data-hook="product-item-name"[^>]*>(.*?)</(?:p|h\d)>', chunk, re.S
            )
            image_match = re.search(r'<img[^>]+src="([^"]+)"[^>]*>', chunk)
            if not (slug_match and url_match and name_match):
                parse_failures += 1
                continue
            slug = unescape(slug_match.group(1))
            sold_out = self._is_sold_out(chunk)
            products[slug] = _product(
                source=self.source,
                product_id=slug,
                name=re.sub(r"<[^>]+>", "", name_match.group(1)).strip(),
                price=self._parse_price(chunk),
                image=_wix_original_image(image_match.group(1)) if image_match else None,
                available=not sold_out,
                url=unescape(url_match.group(1)),
            )
        if not products and 'data-hook="product-list"' not in html:
            raise ValueError(f"CrazyCards product gallery missing from {self.url}")
        if chunks and parse_failures == len(chunks):
            raise ValueError(f"CrazyCards product items were present but could not be parsed from {self.url}")
        return list(products.values())


def requested_category_clients() -> list[Any]:
    return [
        WooCommerceCategoryClient("spielwaren-pokemon-kor", 208),
        WooCommerceCategoryClient("spielwaren-onepiece-kor", 230),
        CrazyCardsCategoryClient("crazycards-onepiece", "https://www.crazycards.eu/onepiece"),
        CrazyCardsCategoryClient("crazycards-pokemon", "https://www.crazycards.eu/pokemon"),
    ]


EXPECTED_NETWORK_ERRORS = (HTTPError, URLError, TimeoutError, OSError, ValueError, KeyError, TypeError)
