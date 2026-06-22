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
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from external_stores import EXPECTED_NETWORK_ERRORS, requested_category_clients


SHOPBY_API = "https://shop-api.e-ncp.com"
POKEMON_STORE = "https://www.pokemonstore.co.kr"
DEFAULT_SHOPBY_CLIENT_ID = "HJGfZ5jPHZk3/PEOkm+/Qw=="
NAVER_SEARCH_API = "https://openapi.naver.com/v1/search/shop.json"
GITHUB_MODELS_API = "https://models.github.ai/inference/chat/completions"
USER_AGENT = "PokemonStoreAvailabilityMonitor/2.0 (+personal-use)"
POKEMON_CARD_CATEGORY_NO = 488339
NAVER_CARD_CATEGORY_ID = "c94139abcef14362997090c5da975e28"


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
    naver_pokemon_queries: tuple[str, ...]
    check_naver_public: bool
    scan_full_catalog: bool
    external_store_interval: int

    @classmethod
    def from_env(cls) -> "Config":
        products = os.getenv("WATCH_PRODUCT_NOS", "")
        watchlist_products = load_watchlist(os.getenv("WATCHLIST_PATH", "watchlist.json"))
        keywords = os.getenv("WATCH_KEYWORDS", "")
        queries = os.getenv(
            "NAVER_XOPLAY_QUERIES",
            "XOPLAY 포켓몬,엑스오플레이 포켓몬,XOPLAY 포켓몬카드,"
            "엑스오플레이 포켓몬카드,포켓몬카드,포켓몬 카드",
        ).split(",")
        pokemon_queries = os.getenv(
            "NAVER_POKEMON_QUERIES",
            "포켓몬 스토어 온라인,포켓몬센터 공식,포켓몬센터,포켓몬 스토어,포켓몬 카드",
        ).split(",")
        return cls(
            webhook_url=os.getenv("DISCORD_WEBHOOK_URL", ""),
            product_nos=tuple(sorted({
                *(int(value.strip()) for value in products.split(",") if value.strip()),
                *watchlist_products,
            })),
            keywords=tuple(value.strip().casefold() for value in keywords.split(",") if value.strip()),
            poll_seconds=max(60, int(os.getenv("POLL_SECONDS", "600"))),
            database_path=os.getenv("DATABASE_PATH", "/data/monitor.db"),
            shopby_client_id=os.getenv("SHOPBY_CLIENT_ID", DEFAULT_SHOPBY_CLIENT_ID),
            notify_on_first_run=os.getenv("NOTIFY_ON_FIRST_RUN", "false").casefold() == "true",
            run_once=os.getenv("RUN_ONCE", "false").casefold() == "true",
            naver_client_id=os.getenv("NAVER_CLIENT_ID", ""),
            naver_client_secret=os.getenv("NAVER_CLIENT_SECRET", ""),
            naver_search_queries=tuple(query.strip() for query in queries if query.strip()),
            naver_pokemon_queries=tuple(
                query.strip() for query in pokemon_queries if query.strip()
            ),
            check_naver_public=os.getenv("CHECK_NAVER_PUBLIC", "true").casefold() == "true",
            scan_full_catalog=os.getenv("SCAN_FULL_CATALOG", "false").casefold() == "true",
            external_store_interval=max(
                300, int(os.getenv("EXTERNAL_STORE_INTERVAL_SECONDS", "600"))
            ),
        )


def request_json(url: str, headers: dict[str, str], timeout: int = 25) -> dict[str, Any]:
    request = Request(url, headers={"User-Agent": USER_AGENT, **headers})
    for attempt in range(4):
        try:
            with urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except HTTPError as error:
            if error.code not in {429, 500, 502, 503, 504} or attempt == 3:
                raise
            delay = 5 * (2 ** attempt)
            logging.warning("HTTP %s from %s; retrying in %ss", error.code, url, delay)
            time.sleep(delay)
    raise RuntimeError("request retry loop ended unexpectedly")


def request_text(url: str, timeout: int = 25) -> str:
    request = Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "ko-KR,ko;q=0.9"})
    for attempt in range(4):
        try:
            with urlopen(request, timeout=timeout) as response:
                return response.read().decode("utf-8", errors="replace")
        except HTTPError as error:
            if error.code != 429 or attempt == 3:
                raise
            delay = 5 * (2 ** attempt)
            logging.warning("Naver rate limited the public feed; retrying in %ss", delay)
            time.sleep(delay)
    raise RuntimeError("text request retry loop ended unexpectedly")


def load_watchlist(path: str) -> set[int]:
    try:
        with open(path, encoding="utf-8") as source:
            values = json.load(source).get("productNos", [])
        return {int(value) for value in values}
    except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
        return set()


def normalized_product(
    *, source: str, product_id: int | str, name: str, price: int | float | None,
    image: str | None, available: bool, status: str, url: str,
    stock_status: str | None = None, available_options: int | None = None,
    sold_out_options: int | None = None,
    currency: str = "KRW",
) -> dict[str, Any]:
    product = {
        "key": f"{source}:{product_id}",
        "source": source,
        "productNo": str(product_id),
        "productName": name,
        "salePrice": price,
        "currency": currency,
        "image": normalize_image(image),
        "isSoldOut": not available,
        "saleStatusType": status,
        "url": url,
        "stockStatus": stock_status or ("AVAILABLE" if available else "SOLD_OUT"),
    }
    if available_options is not None:
        product["availableOptionCount"] = available_options
    if sold_out_options is not None:
        product["soldOutOptionCount"] = sold_out_options
    return product


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

    @staticmethod
    def _option_sale_types(options: list[dict[str, Any]]) -> list[str]:
        sale_types: list[str] = []
        for option in options:
            children = option.get("children") or []
            if children:
                sale_types.extend(PokemonStoreClient._option_sale_types(children))
            elif option.get("saleType"):
                sale_types.append(str(option["saleType"]))
        return sale_types

    def _add_option_stock(self, products: list[dict[str, Any]]) -> None:
        """Replace optimistic search status with authoritative option availability."""
        by_no = {product["productNo"]: product for product in products}
        product_nos = list(by_no)
        for offset in range(0, len(product_nos), 100):
            batch = product_nos[offset:offset + 100]
            data = self._get("/products/options", {"productNos": ",".join(batch)})
            for info in data.get("optionInfos", []):
                product = by_no.get(str(info.get("mallProductNo")))
                if not product:
                    continue
                sale_types = self._option_sale_types(info.get("options") or [])
                if not sale_types:
                    continue
                available_count = sum(value == "AVAILABLE" for value in sale_types)
                sold_out_count = len(sale_types) - available_count
                if available_count and sold_out_count:
                    stock_status = "PARTIAL"
                elif available_count:
                    stock_status = "AVAILABLE"
                else:
                    stock_status = "SOLD_OUT"
                product.update({
                    "isSoldOut": available_count == 0,
                    "stockStatus": stock_status,
                    "availableOptionCount": available_count,
                    "soldOutOptionCount": sold_out_count,
                })

    def new_arrivals(self) -> list[dict[str, Any]]:
        data = self._get("/products/search", {
            "pageNumber": 1, "pageSize": 20, "filter.soldout": "true",
            "categoryNos": POKEMON_CARD_CATEGORY_NO, "order.by": "RECENT_PRODUCT",
        })
        return [self._normalize(product) for product in data.get("items", [])]

    def catalog(self) -> list[dict[str, Any]]:
        products: list[dict[str, Any]] = []
        page = 1
        page_count = 1
        while page <= page_count:
            data = self._get(
                "/products/search",
                {
                    "pageNumber": page, "pageSize": 100, "filter.soldout": "true",
                    "categoryNos": POKEMON_CARD_CATEGORY_NO,
                },
            )
            page_count = int(data.get("pageCount") or 0)
            products.extend(self._normalize(product) for product in data.get("items", []))
            page += 1
        self._add_option_stock(products)
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
    """Reads the newest authoritative products from a public Naver Brand Store."""

    def __init__(
        self, store: str, category_id: str = NAVER_CARD_CATEGORY_ID,
    ) -> None:
        self.store = store
        self.category_id = category_id

    @staticmethod
    def _preloaded_state(html: str) -> dict[str, Any]:
        match = re.search(r"window\.__PRELOADED_STATE__=\s*(.*?)</script>", html, re.S)
        if not match:
            raise ValueError("Naver preloaded product state was not found")
        javascript_object = re.sub(r"\bundefined\b", "null", match.group(1).strip())
        return json.loads(javascript_object)

    def newest(self) -> list[dict[str, Any]]:
        """Fetch the newest 40 products for frequent discovery and stock checks."""
        url = (
            f"https://brand.naver.com/{self.store}/category/{self.category_id}"
            "?st=RECENT&dt=IMAGE&page=1&size=40"
        )
        category = self._preloaded_state(request_text(url))["categoryProducts"]
        return [self._normalize(product) for product in category.get("simpleProducts") or []]

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

    def __init__(
        self, client_id: str, client_secret: str, store_slug: str,
        queries: tuple[str, ...], hosts: tuple[str, ...] = ("smartstore.naver.com",),
        mall_names: tuple[str, ...] = (),
        required_title_terms: tuple[str, ...] = (),
    ) -> None:
        self.headers = {"X-Naver-Client-Id": client_id, "X-Naver-Client-Secret": client_secret}
        self.store_slug = store_slug
        self.queries = queries
        self.hosts = hosts
        self.mall_names = {self._normalize_mall_name(name) for name in mall_names}
        self.required_title_terms = tuple(term.casefold() for term in required_title_terms)

    @staticmethod
    def _normalize_mall_name(name: str) -> str:
        return re.sub(r"[^0-9a-z가-힣]", "", name.casefold())

    def products(self) -> list[dict[str, Any]]:
        found: dict[str, dict[str, Any]] = {}
        examined = 0
        seen_malls: set[str] = set()
        for query in self.queries:
            params = urlencode({"query": query, "display": 100, "start": 1, "sort": "date"})
            for item in request_json(f"{NAVER_SEARCH_API}?{params}", self.headers).get("items", []):
                examined += 1
                link = item.get("link", "")
                mall_name = self._normalize_mall_name(item.get("mallName", ""))
                if mall_name:
                    seen_malls.add(mall_name)
                parsed_link = urlparse(link)
                path_parts = [part for part in parsed_link.path.split("/") if part]
                link_matches = (
                    parsed_link.hostname in self.hosts
                    and bool(path_parts)
                    and path_parts[0].casefold() == self.store_slug.casefold()
                )
                if not link_matches and mall_name not in self.mall_names:
                    continue
                link_id = link.rstrip("/").rsplit("/", 1)[-1]
                product_id = link_id if link_id.isdigit() else item.get("productId")
                name = unescape(re.sub(r"<[^>]+>", "", item.get("title", "")))
                if self.required_title_terms and not any(
                    term in name.casefold() for term in self.required_title_terms
                ):
                    continue
                found[str(product_id)] = normalized_product(
                    source=f"naver-{self.store_slug}", product_id=product_id, name=name,
                    price=int(item["lprice"]) if item.get("lprice") else None,
                    image=item.get("image"), available=True, status="SEARCH_RESULT", url=link,
                    stock_status="UNKNOWN",
                )
        logging.info(
            "Naver Search %s examined %s results and accepted %s; malls=%s",
            self.store_slug, examined, len(found), ",".join(sorted(seen_malls)[:12]),
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
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS check_times (feed TEXT PRIMARY KEY, checked_at REAL NOT NULL)"
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

    def clear_source_once(self, source: str, scope_marker: str) -> None:
        """Discard products from an older, broader scope once during migration."""
        if self.feed_initialized(scope_marker):
            return
        keys = [
            row[0] for row in self.db.execute("SELECT product_key, payload FROM observations")
            if json.loads(row[1]).get("source") == source
        ]
        self.db.executemany("DELETE FROM observations WHERE product_key = ?", ((key,) for key in keys))
        self.db.execute("INSERT OR IGNORE INTO initialized_feeds(feed) VALUES(?)", (scope_marker,))
        self.db.commit()

    def retain_source_products(self, source: str, product_keys: set[str]) -> None:
        """Remove products that no longer belong to an authoritative category catalogue."""
        keys = [
            row[0] for row in self.db.execute("SELECT product_key, payload FROM observations")
            if json.loads(row[1]).get("source") == source and row[0] not in product_keys
        ]
        self.db.executemany("DELETE FROM observations WHERE product_key = ?", ((key,) for key in keys))
        self.db.commit()

    def check_due(self, feed: str, interval_seconds: int, now: float | None = None) -> bool:
        row = self.db.execute(
            "SELECT checked_at FROM check_times WHERE feed = ?", (feed,)
        ).fetchone()
        return row is None or (now if now is not None else time.time()) - float(row[0]) >= interval_seconds

    def mark_checked(self, feed: str, checked_at: float | None = None) -> None:
        self.db.execute(
            "INSERT INTO check_times(feed, checked_at) VALUES(?, ?) "
            "ON CONFLICT(feed) DO UPDATE SET checked_at = excluded.checked_at",
            (feed, checked_at if checked_at is not None else time.time()),
        )
        self.db.commit()


def normalize_image(url: str | None) -> str | None:
    if not url:
        return None
    return f"https:{url}" if url.startswith("//") else url


def product_url(product_no: int) -> str:
    return f"{POKEMON_STORE}/pages/product/product-detail.html?productNo={product_no}"


def is_available(product: dict[str, Any]) -> bool:
    stock_status = product.get("stockStatus")
    return (
        stock_status in {"AVAILABLE", "PARTIAL"}
        and not bool(product.get("isSoldOut"))
        and product.get("saleStatusType") in {"ONSALE", "SALE"}
    )


def translate_product_name(name: str) -> str | None:
    token = os.getenv("GITHUB_MODELS_TOKEN", "")
    if not token:
        return None
    payload = {
        "model": "openai/gpt-4o-mini",
        "temperature": 0,
        "max_tokens": 100,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Translate Korean Pokémon merchandise product titles into concise natural English. "
                    "Preserve official Pokémon names, edition names, model numbers, and product types. "
                    "Return only the translated title without quotation marks."
                ),
            },
            {"role": "user", "content": name},
        ],
    }
    request = Request(
        GITHUB_MODELS_API,
        data=json.dumps(payload).encode(),
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": "2026-03-10",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            translated = json.load(response)["choices"][0]["message"]["content"].strip()
        return translated if translated and translated.casefold() != name.casefold() else None
    except (HTTPError, URLError, TimeoutError, KeyError, IndexError, json.JSONDecodeError):
        logging.warning("Product-title translation failed; sending original title", exc_info=True)
        return None


def send_discord(webhook_url: str, title: str, product: dict[str, Any], color: int) -> None:
    if not webhook_url:
        logging.info("Discord is not configured; skipped alert: %s", product["productName"])
        return
    price = product.get("salePrice")
    currency = product.get("currency", "KRW")
    if isinstance(price, (int, float)):
        price_text = f"€{price:,.2f}" if currency == "EUR" else f"₩{price:,.0f}"
    else:
        price_text = "Unknown"
    translated = translate_product_name(product["productName"])
    description = (
        f"**{translated}**\nOriginal: {product['productName']}" if translated else product["productName"]
    )
    embed: dict[str, Any] = {
        "title": title, "description": description, "url": product["url"], "color": color,
        "fields": [
            {"name": "Store", "value": product["source"], "inline": True},
            {"name": "Product", "value": f"#{product['productNo']}", "inline": True},
            {"name": "Price", "value": price_text, "inline": True},
        ],
        "footer": {"text": "Pokémon product monitor"},
    }
    if product.get("image"):
        embed["thumbnail"] = {"url": product["image"]}
    request = Request(
        webhook_url, data=json.dumps({"embeds": [embed]}).encode(),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT}, method="POST",
    )
    for attempt in range(2):
        try:
            with urlopen(request, timeout=20) as response:
                if response.status not in (200, 204):
                    logging.warning("Discord returned HTTP %s; alert skipped", response.status)
            return
        except HTTPError as error:
            if error.code == 429 and attempt == 0:
                retry_after = min(float(error.headers.get("Retry-After", "2")), 10)
                logging.warning("Discord rate limited an alert; retrying in %.1fs", retry_after)
                time.sleep(retry_after)
                continue
            logging.warning("Discord alert failed with HTTP %s; monitor will continue", error.code)
            return
        except (URLError, TimeoutError, ValueError):
            logging.warning("Discord alert failed; monitor will continue", exc_info=True)
            return


def keyword_match(product: dict[str, Any], keywords: tuple[str, ...]) -> bool:
    return not keywords or any(word in product["productName"].casefold() for word in keywords)


def observe_products(
    config: Config, state: State, products: list[dict[str, Any]], *, feed: str,
    reliable_stock: bool = True, notify_new: bool = True, update_existing: bool = True,
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
        if previous is None or update_existing:
            state.put(product)
    state.mark_feed_initialized(feed)


def check_once(config: Config, pokemon: PokemonStoreClient, state: State) -> None:
    state.clear_source_once("naver-pokemon", "scope:naver-pokemon-card-category-v1")
    observe_products(config, state, pokemon.new_arrivals(), feed="pokemonstore-card-arrivals")
    for product_no in config.product_nos:
        observe_products(
            config, state, [pokemon.product(product_no)], feed="pokemonstore-watchlist", notify_new=False
        )

    if config.scan_full_catalog:
        logging.info("Refreshing Pokémon Store card category catalog")
        card_products = pokemon.catalog()
        if card_products:
            state.retain_source_products(
                "pokemonstore", {product["key"] for product in card_products}
            )
        observe_products(
            config, state, card_products, feed="pokemonstore-card-catalog",
            reliable_stock=False,
        )

    if config.check_naver_public:
        naver_pokemon = NaverBrandCategoryClient("pokemon")
        try:
            observe_products(
                config, state, naver_pokemon.newest(), feed="naver-pokemon-card-category"
            )
        except (HTTPError, URLError, TimeoutError, KeyError, ValueError, RuntimeError):
            logging.exception("Naver public feed is temporarily unavailable; retained previous state")

    if config.naver_client_id and config.naver_client_secret:
        pokemon_search = NaverShoppingSearchClient(
            config.naver_client_id, config.naver_client_secret, "pokemon",
            config.naver_pokemon_queries,
            hosts=("brand.naver.com", "smartstore.naver.com"),
            mall_names=("포켓몬 스토어 온라인", "포켓몬스토어온라인"),
            required_title_terms=("카드", "덱", "슬리브"),
        )
        observe_products(
            config, state, pokemon_search.products(), feed="naver-pokemon-search-card-v3",
            reliable_stock=False, update_existing=False,
        )
        xoplay = NaverShoppingSearchClient(
            config.naver_client_id, config.naver_client_secret, "xoplay", config.naver_search_queries,
            mall_names=("XOPLAY", "엑스오플레이"),
        )
        observe_products(
            config, state, xoplay.products(), feed="naver-xoplay-search-v2", reliable_stock=False
        )
    else:
        logging.info("Naver Search API credentials absent; skipping fast Naver discovery")

    if state.check_due("external-card-categories", config.external_store_interval):
        for client in requested_category_clients():
            try:
                products = client.products()
                state.retain_source_products(
                    client.source, {product["key"] for product in products}
                )
                observe_products(
                    config, state, products, feed=f"external:{client.source}", reliable_stock=True
                )
                logging.info("External category %s: %s products", client.source, len(products))
            except EXPECTED_NETWORK_ERRORS:
                logging.exception("External category %s is temporarily unavailable", client.source)
        state.mark_checked("external-card-categories")


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
                raise
        if config.run_once:
            break
        for _ in range(config.poll_seconds):
            if stopping:
                break
            time.sleep(1)


if __name__ == "__main__":
    main()
