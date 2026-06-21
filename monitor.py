#!/usr/bin/env python3
"""Monitor Pokémon Store Korea and selected Naver storefront categories."""

from __future__ import annotations

import json
import logging
import os
import re
import signal
import sqlite3
import time
from dataclasses import dataclass
from html import unescape
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


SHOPBY_API = "https://shop-api.e-ncp.com"
POKEMON_STORE = "https://www.pokemonstore.co.kr"
DEFAULT_SHOPBY_CLIENT_ID = "HJGfZ5jPHZk3/PEOkm+/Qw=="
NAVER_SEARCH_API = "https://openapi.naver.com/v1/search/shop.json"
USER_AGENT = "PokemonStoreAvailabilityMonitor/2.0 (+personal-use)"


@dataclass(frozen=True)
class Config:
    webhook_url: str
    product_nos: tuple[int, ...]
    keywords: tuple[str, ...]
    poll_seconds: int
    database_path: str
    shopby_client_id: str
    notify_on_first_run: bool
    run_once: bool
    naver_client_id: str
    naver_client_secret: str
    naver_search_queries: tuple[str, ...]
    scan_full_catalog: bool

    @classmethod
    def from_env(cls) -> "Config":
        products = os.getenv("WATCH_PRODUCT_NOS", "")
        keywords = os.getenv("WATCH_KEYWORDS", "")
        queries = os.getenv("NAVER_XOPLAY_QUERIES", "포켓몬카드,포켓몬 카드").split(",")
        return cls(
            webhook_url=os.getenv("DISCORD_WEBHOOK_URL", ""),
            product_nos=tuple(int(value.strip()) for value in products.split(",") if value.strip()),
            keywords=tuple(value.strip().casefold() for value in keywords.split(",") if value.strip()),
            poll_seconds=max(60, int(os.getenv("POLL_SECONDS", "600"))),
            database_path=os.getenv("DATABASE_PATH", "/data/monitor.db"),
            shopby_client_id=os.getenv("SHOPBY_CLIENT_ID", DEFAULT_SHOPBY_CLIENT_ID),
            notify_on_first_run=os.getenv("NOTIFY_ON_FIRST_RUN", "false").casefold() == "true",
            run_once=os.getenv("RUN_ONCE", "false").casefold() == "true",
            naver_client_id=os.getenv("NAVER_CLIENT_ID", ""),
            naver_client_secret=os.getenv("NAVER_CLIENT_SECRET", ""),
            naver_search_queries=tuple(query.strip() for query in queries if query.strip()),
            scan_full_catalog=os.getenv("SCAN_FULL_CATALOG", "false").casefold() == "true",
        )


def request_json(url: str, headers: dict[str, str], timeout: int = 25) -> dict[str, Any]:
    request = Request(url, headers={"User-Agent": USER_AGENT, **headers})
    with urlopen(request, timeout=timeout) as response:
        return json.load(response)


def request_text(url: str, timeout: int = 25) -> str:
    request = Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "ko-KR,ko;q=0.9"})
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def normalized_product(
    *, source: str, product_id: int | str, name: str, price: int | float | None,
    image: str | None, available: bool, status: str, url: str,
) -> dict[str, Any]:
    return {
        "key": f"{source}:{product_id}",
        "source": source,
        "productNo": str(product_id),
        "productName": name,
        "salePrice": price,
        "image": normalize_image(image),
        "isSoldOut": not available,
        "saleStatusType": status,
        "url": url,
    }


class PokemonStoreClient:
    def __init__(self, client_id: str) -> None:
        self.headers = {
            "Accept": "application/json", "Version": "1.0", "Clientid": client_id, "Platform": "PC"
        }

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{SHOPBY_API}{path}"
        if params:
            url = f"{url}?{urlencode(params)}"
        return request_json(url, self.headers)

    @staticmethod
    def _normalize(product: dict[str, Any]) -> dict[str, Any]:
        product_no = int(product["productNo"])
        images = product.get("listImageUrls") or product.get("imageUrls") or []
        available = not bool(product.get("isSoldOut")) and product.get("saleStatusType") == "ONSALE"
        return normalized_product(
            source="pokemonstore", product_id=product_no, name=product["productName"],
            price=product.get("salePrice"), image=images[0] if images else product.get("image"),
            available=available, status=product.get("saleStatusType", "UNKNOWN"),
            url=f"{POKEMON_STORE}/pages/product/product-detail.html?productNo={product_no}",
        )

    def new_arrivals(self) -> list[dict[str, Any]]:
        data = self._get(
            "/display/sections/ids/SCPC0001/products", {"pageNumber": 1, "pageSize": 20}
        )
        return [self._normalize(product) for product in data.get("products", [])]

    def catalog(self) -> list[dict[str, Any]]:
        products: list[dict[str, Any]] = []
        page = 1
        page_count = 1
        while page <= page_count:
            data = self._get("/products/search", {"pageNumber": page, "pageSize": 100})
            page_count = int(data.get("pageCount") or 0)
            products.extend(self._normalize(product) for product in data.get("items", []))
            page += 1
        return products

    def product(self, product_no: int) -> dict[str, Any]:
        data = self._get(f"/products/{product_no}")
        base, status, price = data["baseInfo"], data["status"], data["price"]
        return self._normalize({
            "productNo": base["productNo"], "productName": base["productName"],
            "salePrice": price.get("salePrice"), "imageUrls": base.get("imageUrls") or [],
            "isSoldOut": bool(status.get("soldout")), "saleStatusType": status.get("saleStatusType"),
        })


class NaverBrandCategoryClient:
    """Reads the product state Naver embeds in its public brand-store category pages."""

    def __init__(self, store: str, category_id: str) -> None:
        self.store = store
        self.category_id = category_id

    @staticmethod
    def _preloaded_state(html: str) -> dict[str, Any]:
        match = re.search(r"window\.__PRELOADED_STATE__=\s*(.*?)</script>", html, re.S)
        if not match:
            raise ValueError("Naver preloaded product state was not found")
        javascript_object = re.sub(r"\bundefined\b", "null", match.group(1).strip())
        return json.loads(javascript_object)

    def products(self) -> list[dict[str, Any]]:
        # Naver server-renders only the first category page. RECENT makes that page
        # useful for discovery while retaining authoritative stock for its 40 items.
        url = (
            f"https://brand.naver.com/{self.store}/category/{self.category_id}"
            "?st=RECENT&dt=IMAGE&page=1&size=40"
        )
        category = self._preloaded_state(request_text(url))["categoryProducts"]
        products = category.get("simpleProducts") or []
        return [self._normalize(product) for product in products]

    def _normalize(self, product: dict[str, Any]) -> dict[str, Any]:
        public_id = int(product["id"])
        stock = int(product.get("stockQuantity") or 0)
        available = (
            product.get("productStatusType") == "SALE"
            and product.get("channelProductDisplayStatusType") == "ON"
            and bool(product.get("displayable"))
            and stock > 0
        )
        return normalized_product(
            source=f"naver-{self.store}", product_id=public_id,
            name=product.get("dispName") or product["name"], price=product.get("dispSalePrice"),
            image=product.get("representativeImageUrl"), available=available,
            status=product.get("productStatusType", "UNKNOWN"),
            url=f"https://brand.naver.com/{self.store}/products/{public_id}",
        )


class NaverShoppingSearchClient:
    """Official Search API fallback; useful for discoveries, not authoritative stock."""

    def __init__(self, client_id: str, client_secret: str, store_slug: str, queries: tuple[str, ...]) -> None:
        self.headers = {"X-Naver-Client-Id": client_id, "X-Naver-Client-Secret": client_secret}
        self.store_slug = store_slug
        self.queries = queries

    def products(self) -> list[dict[str, Any]]:
        found: dict[str, dict[str, Any]] = {}
        for query in self.queries:
            params = urlencode({"query": query, "display": 100, "start": 1, "sort": "date"})
            for item in request_json(f"{NAVER_SEARCH_API}?{params}", self.headers).get("items", []):
                link = item.get("link", "")
                if f"smartstore.naver.com/{self.store_slug}" not in link:
                    continue
                product_id = item.get("productId") or link.rstrip("/").rsplit("/", 1)[-1]
                name = unescape(re.sub(r"<[^>]+>", "", item.get("title", "")))
                found[str(product_id)] = normalized_product(
                    source=f"naver-{self.store_slug}", product_id=product_id, name=name,
                    price=int(item["lprice"]) if item.get("lprice") else None,
                    image=item.get("image"), available=True, status="SEARCH_RESULT", url=link,
                )
        return list(found.values())


class State:
    def __init__(self, path: str) -> None:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS observations (product_key TEXT PRIMARY KEY, payload TEXT NOT NULL)"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS initialized_feeds (feed TEXT PRIMARY KEY)"
        )
        self.db.commit()

    def get(self, product_key: str) -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT payload FROM observations WHERE product_key = ?", (product_key,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, product: dict[str, Any]) -> None:
        payload = json.dumps(product, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        existing = self.db.execute(
            "SELECT payload FROM observations WHERE product_key = ?", (product["key"],)
        ).fetchone()
        if existing and existing[0] == payload:
            return
        self.db.execute(
            "INSERT INTO observations(product_key, payload) VALUES(?, ?) "
            "ON CONFLICT(product_key) DO UPDATE SET payload = excluded.payload",
            (product["key"], payload),
        )
        self.db.commit()

    def all(self) -> list[dict[str, Any]]:
        return [json.loads(row[0]) for row in self.db.execute("SELECT payload FROM observations")]

    def feed_initialized(self, feed: str) -> bool:
        return self.db.execute(
            "SELECT 1 FROM initialized_feeds WHERE feed = ?", (feed,)
        ).fetchone() is not None

    def mark_feed_initialized(self, feed: str) -> None:
        self.db.execute("INSERT OR IGNORE INTO initialized_feeds(feed) VALUES(?)", (feed,))
        self.db.commit()


def normalize_image(url: str | None) -> str | None:
    if not url:
        return None
    return f"https:{url}" if url.startswith("//") else url


def product_url(product_no: int) -> str:
    return f"{POKEMON_STORE}/pages/product/product-detail.html?productNo={product_no}"


def is_available(product: dict[str, Any]) -> bool:
    return not bool(product.get("isSoldOut")) and product.get("saleStatusType") in {
        "ONSALE", "SALE", "SEARCH_RESULT"
    }


def send_discord(webhook_url: str, title: str, product: dict[str, Any], color: int) -> None:
    if not webhook_url:
        logging.info("Discord is not configured; skipped alert: %s", product["productName"])
        return
    price = product.get("salePrice")
    embed: dict[str, Any] = {
        "title": title, "description": product["productName"], "url": product["url"], "color": color,
        "fields": [
            {"name": "Store", "value": product["source"], "inline": True},
            {"name": "Price", "value": f"₩{price:,.0f}" if isinstance(price, (int, float)) else "Unknown", "inline": True},
        ],
        "footer": {"text": "Pokémon product monitor"},
    }
    if product.get("image"):
        embed["thumbnail"] = {"url": product["image"]}
    request = Request(
        webhook_url, data=json.dumps({"embeds": [embed]}).encode(),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT}, method="POST",
    )
    with urlopen(request, timeout=20) as response:
        if response.status not in (200, 204):
            raise RuntimeError(f"Discord returned HTTP {response.status}")


def keyword_match(product: dict[str, Any], keywords: tuple[str, ...]) -> bool:
    return not keywords or any(word in product["productName"].casefold() for word in keywords)


def observe_products(
    config: Config, state: State, products: list[dict[str, Any]], *, feed: str,
    reliable_stock: bool = True, notify_new: bool = True,
) -> None:
    initialized = state.feed_initialized(feed)
    for product in products:
        previous = state.get(product["key"])
        if previous is None and notify_new and keyword_match(product, config.keywords):
            if initialized or config.notify_on_first_run:
                send_discord(config.webhook_url, "✨ New product", product, 0xFFCB05)
            else:
                logging.info("Primed %s", product["key"])
        elif reliable_stock and previous is not None and not is_available(previous) and is_available(product):
            send_discord(config.webhook_url, "✅ Back in stock", product, 0x3BA55D)
        state.put(product)
    state.mark_feed_initialized(feed)


def check_once(config: Config, pokemon: PokemonStoreClient, state: State) -> None:
    observe_products(config, state, pokemon.new_arrivals(), feed="pokemonstore-arrivals")
    for product_no in config.product_nos:
        observe_products(
            config, state, [pokemon.product(product_no)], feed="pokemonstore-watchlist", notify_new=False
        )

    if config.scan_full_catalog:
        logging.info("Refreshing full Pokémon Store catalog")
        observe_products(
            config, state, pokemon.catalog(), feed="pokemonstore-catalog",
            reliable_stock=False, notify_new=False,
        )

    naver_pokemon = NaverBrandCategoryClient("pokemon", "c94139abcef14362997090c5da975e28")
    observe_products(config, state, naver_pokemon.products(), feed="naver-pokemon-recent")

    if config.naver_client_id and config.naver_client_secret:
        xoplay = NaverShoppingSearchClient(
            config.naver_client_id, config.naver_client_secret, "xoplay", config.naver_search_queries
        )
        observe_products(
            config, state, xoplay.products(), feed="naver-xoplay-search", reliable_stock=False
        )
    else:
        logging.info("Naver Search API credentials absent; skipping Xoplay discovery")


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    config = Config.from_env()
    pokemon = PokemonStoreClient(config.shopby_client_id)
    state = State(config.database_path)
    stopping = False

    def stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    while not stopping:
        try:
            check_once(config, pokemon, state)
        except (HTTPError, URLError, TimeoutError, KeyError, ValueError, RuntimeError) as error:
            logging.exception("Monitor check failed: %s", error)
        if config.run_once:
            break
        for _ in range(config.poll_seconds):
            if stopping:
                break
            time.sleep(1)


if __name__ == "__main__":
    main()
