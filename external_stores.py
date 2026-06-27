"""Public category clients for the requested German and EU card shops."""

from __future__ import annotations

import gzip
import json
import re
import time
from html import unescape
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


USER_AGENT = "PokemonStoreAvailabilityMonitor/3.0 (+personal-use; EU-card-shops)"


def _wix_original_image(url: str | None) -> str | None:
    """Return Wix's original media asset instead of its tiny blurred gallery thumbnail."""
    if not url:
        return None
    match = re.match(r"(https://static\.wixstatic\.com/media/[^/?]+)", unescape(url))
    return match.group(1) if match else unescape(url)


def _strip_html(value: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", value))).strip()


def _slug_to_name(slug: str) -> str:
    return unescape(slug.rsplit("/", 1)[-1].replace("-", " ")).strip().title()


def _parse_euro_price(value: str) -> float | None:
    text = unescape(value).replace("\xa0", " ")
    price_match = re.search(r"(\d{1,3}(?:[.\s]\d{3})*,\d{2})\s*€", text)
    if not price_match:
        return None
    return float(price_match.group(1).replace(".", "").replace(" ", "").replace(",", "."))


def _absolute_url(url: str | None, base: str) -> str | None:
    if not url:
        return None
    return urljoin(base, unescape(url))


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


class CardmarketSellerOffersClient:
    """Parse server-rendered Cardmarket seller offer pages by product link."""

    PRODUCT_LINK_RE = re.compile(r'href="(?P<href>/en/(?:Pokemon|OnePiece)/Products/[^"]+)"', re.I)

    def __init__(self, source: str, urls: list[str]) -> None:
        self.source = source
        self.urls = urls

    @staticmethod
    def _product_id(href: str) -> str:
        return unescape(href.split("?", 1)[0].split("#", 1)[0]).removeprefix("/en/")

    @staticmethod
    def _name_from_chunk(href: str, chunk: str) -> str:
        anchor_match = re.search(r'<a\b[^>]*href="' + re.escape(href) + r'"[^>]*>(.*?)</a>', chunk, re.S)
        anchor = anchor_match.group(0) if anchor_match else ""
        title_match = re.search(r'\btitle="([^"]+)"', anchor)
        text = _strip_html(anchor_match.group(1)) if anchor_match else ""
        if text and not text.startswith("/"):
            return text
        if title_match:
            return unescape(title_match.group(1)).strip()
        return _slug_to_name(href)

    @staticmethod
    def _image_from_chunk(chunk: str, page_url: str) -> str | None:
        image_match = re.search(r'<img[^>]+(?:data-original|data-src|src)="([^"]+)"', chunk)
        return _absolute_url(image_match.group(1), page_url) if image_match else None

    def _page_products(self, page_url: str) -> dict[str, dict[str, Any]]:
        html = _request(page_url, as_json=False)
        matches = list(self.PRODUCT_LINK_RE.finditer(html))
        products: dict[str, dict[str, Any]] = {}
        for index, match in enumerate(matches):
            href = unescape(match.group("href"))
            end = matches[index + 1].start() if index + 1 < len(matches) else min(len(html), match.start() + 4000)
            chunk = html[match.start():end]
            product_id = self._product_id(href)
            product = _product(
                source=self.source,
                product_id=product_id,
                name=self._name_from_chunk(href, chunk),
                price=_parse_euro_price(chunk),
                image=self._image_from_chunk(chunk, page_url),
                available=True,
                url=_absolute_url(href, page_url) or page_url,
            )
            existing = products.get(product_id)
            if existing:
                if existing.get("salePrice") is None and product.get("salePrice") is not None:
                    product["image"] = product.get("image") or existing.get("image")
                    products[product_id] = product
                elif existing.get("image") is None and product.get("image"):
                    existing["image"] = product["image"]
            else:
                products[product_id] = product
        if not products and not re.search(r"Offers|Angebote|No articles|Keine Artikel", html, re.I):
            raise ValueError(f"Cardmarket seller offers could not be parsed from {page_url}")
        return products

    def products(self) -> list[dict[str, Any]]:
        products: dict[str, dict[str, Any]] = {}
        for page_url in self.urls:
            products.update(self._page_products(page_url))
        return list(products.values())


CARDMARKET_CRAZYCARDS_POKEMON_URLS = [
    "https://www.cardmarket.com/en/Pokemon/Users/CrazyCardsEU/Offers/Booster-Boxes?sortBy=name_asc&idLanguage=10",
    "https://www.cardmarket.com/en/Pokemon/Users/CrazyCardsEU/Offers/Theme-Decks?sortBy=name_asc&idLanguage=10",
    "https://www.cardmarket.com/en/Pokemon/Users/CrazyCardsEU/Offers/Box-Sets?sortBy=name_asc&idLanguage=10",
]
CARDMARKET_CARDCOFFEE_POKEMON_URLS = [
    "https://www.cardmarket.com/en/Pokemon/Users/Card-Coffee/Offers/Booster-Boxes?sortBy=name_asc&idLanguage=10",
    "https://www.cardmarket.com/en/Pokemon/Users/Card-Coffee/Offers/Box-Sets?sortBy=name_asc&idLanguage=10",
]
CARDMARKET_CARDCOFFEE_ONEPIECE_URLS = [
    "https://www.cardmarket.com/en/OnePiece/Users/Card-Coffee/Offers/Booster-Boxes?sortBy=name_asc&idLanguage=10",
]
CARDMARKET_CRAZYCARDS_ONEPIECE_URLS = [
    "https://www.cardmarket.com/en/OnePiece/Users/CrazyCardsEU/Offers/Booster-Boxes?sortBy=name_asc&idLanguage=10",
]


def requested_category_clients() -> list[Any]:
    return [
        WooCommerceCategoryClient("spielwaren-pokemon-kor", 208),
        WooCommerceCategoryClient("spielwaren-onepiece-kor", 230),
        CrazyCardsCategoryClient("crazycards-onepiece", "https://www.crazycards.eu/onepiece"),
        CrazyCardsCategoryClient("crazycards-pokemon", "https://www.crazycards.eu/pokemon"),
        CardmarketSellerOffersClient("cardmarket-crazycards-pokemon", CARDMARKET_CRAZYCARDS_POKEMON_URLS),
        CardmarketSellerOffersClient("cardmarket-cardcoffee-pokemon", CARDMARKET_CARDCOFFEE_POKEMON_URLS),
        CardmarketSellerOffersClient("cardmarket-cardcoffee-onepiece", CARDMARKET_CARDCOFFEE_ONEPIECE_URLS),
        CardmarketSellerOffersClient("cardmarket-crazycards-onepiece", CARDMARKET_CRAZYCARDS_ONEPIECE_URLS),
    ]


EXPECTED_NETWORK_ERRORS = (HTTPError, URLError, TimeoutError, OSError, ValueError, KeyError, TypeError)
