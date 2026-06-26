#!/usr/bin/env python3
"""Monitor the remaining EU card shops and send Discord alerts."""

from __future__ import annotations

import json
import logging
import os
import signal
import sqlite3
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from external_stores import EXPECTED_NETWORK_ERRORS, requested_category_clients


USER_AGENT = "PokemonStoreAvailabilityMonitor/3.0 (+personal-use; EU-card-shops)"
LIVE_SOURCES = {
    "crazycards-onepiece",
    "crazycards-pokemon",
    "spielwaren-onepiece-kor",
    "spielwaren-pokemon-kor",
}


@dataclass(frozen=True)
class Config:
    webhook_url: str
    keywords: tuple[str, ...]
    poll_seconds: int
    database_path: str
    notify_on_first_run: bool
    run_once: bool
    external_store_interval: int

    @classmethod
    def from_env(cls) -> "Config":
        keywords = os.getenv("WATCH_KEYWORDS", "")
        return cls(
            webhook_url=os.getenv("DISCORD_WEBHOOK_URL", ""),
            keywords=tuple(value.strip().casefold() for value in keywords.split(",") if value.strip()),
            poll_seconds=max(60, int(os.getenv("POLL_SECONDS", "300"))),
            database_path=os.getenv("DATABASE_PATH", "/data/monitor.db"),
            notify_on_first_run=os.getenv("NOTIFY_ON_FIRST_RUN", "false").casefold() == "true",
            run_once=os.getenv("RUN_ONCE", "false").casefold() == "true",
            external_store_interval=max(
                300, int(os.getenv("EXTERNAL_STORE_INTERVAL_SECONDS", "300"))
            ),
        )


class State:
    def __init__(self, path: str) -> None:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS observations "
            "(product_key TEXT PRIMARY KEY, payload TEXT NOT NULL)"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS initialized_feeds (feed TEXT PRIMARY KEY)"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS check_times "
            "(feed TEXT PRIMARY KEY, checked_at REAL NOT NULL)"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS success_times "
            "(feed TEXT PRIMARY KEY, succeeded_at REAL NOT NULL)"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS feed_errors "
            "(feed TEXT PRIMARY KEY, failing INTEGER NOT NULL, message TEXT, updated_at REAL NOT NULL)"
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

    def source_product_count(self, source: str) -> int:
        return int(self.db.execute(
            "SELECT COUNT(*) FROM observations WHERE product_key LIKE ?",
            (f"{source}:%",),
        ).fetchone()[0])

    def feed_initialized(self, feed: str) -> bool:
        return self.db.execute(
            "SELECT 1 FROM initialized_feeds WHERE feed = ?", (feed,)
        ).fetchone() is not None

    def mark_feed_initialized(self, feed: str) -> None:
        self.db.execute("INSERT OR IGNORE INTO initialized_feeds(feed) VALUES(?)", (feed,))
        self.db.commit()

    def ensure_feeds_initialized_from_existing_products(
        self, source_to_feeds: dict[str, list[str]]
    ) -> None:
        for source, feeds in source_to_feeds.items():
            if self.source_product_count(source):
                for feed in feeds:
                    self.mark_feed_initialized(feed)

    def retain_source_products(self, source: str, product_keys: set[str]) -> None:
        keys = [
            row[0]
            for row in self.db.execute("SELECT product_key, payload FROM observations")
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

    def mark_succeeded(self, feed: str, succeeded_at: float | None = None) -> None:
        self.db.execute(
            "INSERT INTO success_times(feed, succeeded_at) VALUES(?, ?) "
            "ON CONFLICT(feed) DO UPDATE SET succeeded_at = excluded.succeeded_at",
            (feed, succeeded_at if succeeded_at is not None else time.time()),
        )
        self.db.commit()

    def mark_feed_error(self, feed: str, message: str) -> bool:
        row = self.db.execute(
            "SELECT failing FROM feed_errors WHERE feed = ?", (feed,)
        ).fetchone()
        already_failing = bool(row and row[0])
        self.db.execute(
            "INSERT INTO feed_errors(feed, failing, message, updated_at) VALUES(?, 1, ?, ?) "
            "ON CONFLICT(feed) DO UPDATE SET failing = 1, message = excluded.message, "
            "updated_at = excluded.updated_at",
            (feed, message[:500], time.time()),
        )
        self.db.commit()
        return not already_failing

    def clear_feed_error(self, feed: str) -> bool:
        row = self.db.execute(
            "SELECT failing FROM feed_errors WHERE feed = ?", (feed,)
        ).fetchone()
        was_failing = bool(row and row[0])
        self.db.execute(
            "INSERT INTO feed_errors(feed, failing, message, updated_at) VALUES(?, 0, NULL, ?) "
            "ON CONFLICT(feed) DO UPDATE SET failing = 0, message = NULL, updated_at = excluded.updated_at",
            (feed, time.time()),
        )
        self.db.commit()
        return was_failing


def is_available(product: dict[str, Any]) -> bool:
    return (
        product.get("stockStatus") == "AVAILABLE"
        and not bool(product.get("isSoldOut"))
        and product.get("saleStatusType") == "SALE"
    )


def format_price(product: dict[str, Any]) -> str:
    price = product.get("salePrice")
    if isinstance(price, (int, float)):
        return f"€{price:,.2f}"
    return "Unknown"


def send_discord_payload(webhook_url: str, payload: dict[str, Any]) -> bool:
    if not webhook_url:
        logging.info("Discord is not configured; skipped alert")
        return False
    request = Request(
        webhook_url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    for attempt in range(2):
        try:
            with urlopen(request, timeout=20) as response:
                if response.status not in (200, 204):
                    logging.warning("Discord returned HTTP %s; alert skipped", response.status)
                    return False
            return True
        except HTTPError as error:
            if error.code == 429 and attempt == 0:
                retry_after = min(float(error.headers.get("Retry-After", "2")), 10)
                logging.warning("Discord rate limited an alert; retrying in %.1fs", retry_after)
                time.sleep(retry_after)
                continue
            logging.warning("Discord alert failed with HTTP %s; monitor will continue", error.code)
            return False
        except (URLError, TimeoutError, ValueError):
            logging.warning("Discord alert failed; monitor will continue", exc_info=True)
            return False
    return False


def send_discord(webhook_url: str, title: str, product: dict[str, Any], color: int) -> bool:
    embed: dict[str, Any] = {
        "title": title,
        "description": product["productName"],
        "url": product["url"],
        "color": color,
        "fields": [
            {"name": "Store", "value": product["source"], "inline": True},
            {"name": "Product", "value": f"#{product['productNo']}", "inline": True},
            {"name": "Price", "value": format_price(product), "inline": True},
        ],
        "footer": {"text": "EU card shop monitor"},
    }
    if product.get("image"):
        embed["thumbnail"] = {"url": product["image"]}
    return send_discord_payload(webhook_url, {"embeds": [embed]})


def send_store_status(webhook_url: str, title: str, source: str, description: str, color: int) -> bool:
    return send_discord_payload(
        webhook_url,
        {
            "embeds": [
                {
                    "title": title,
                    "description": description,
                    "color": color,
                    "fields": [{"name": "Store", "value": source, "inline": True}],
                    "footer": {"text": "EU card shop monitor"},
                }
            ]
        },
    )


def keyword_match(product: dict[str, Any], keywords: tuple[str, ...]) -> bool:
    return not keywords or any(word in product["productName"].casefold() for word in keywords)


def observe_products(
    config: Config,
    state: State,
    products: list[dict[str, Any]],
    *,
    feed: str,
) -> None:
    initialized = state.feed_initialized(feed)
    for product in products:
        previous = state.get(product["key"])
        if previous is None and keyword_match(product, config.keywords):
            if initialized or config.notify_on_first_run:
                send_discord(config.webhook_url, "✨ New product", product, 0xFFCB05)
            else:
                logging.info("Primed %s", product["key"])
        elif previous is not None and not is_available(previous) and is_available(product):
            send_discord(config.webhook_url, "✅ Back in stock", product, 0x3BA55D)
        state.put(product)
    state.mark_feed_initialized(feed)


def checked_products(source: str, products: list[dict[str, Any]], state: State) -> list[dict[str, Any]]:
    if products:
        return products
    previous_count = state.source_product_count(source)
    if previous_count:
        raise ValueError(
            f"{source} returned zero products but {previous_count} products are already tracked; "
            "treating this as a scan failure to avoid wiping dashboard state"
        )
    raise ValueError(f"{source} returned zero products on first scan")


def check_once(config: Config, state: State) -> None:
    clients = requested_category_clients()
    state.ensure_feeds_initialized_from_existing_products(
        {client.source: [f"external:{client.source}"] for client in clients}
    )
    now = time.time()
    for client in clients:
        feed = f"external:{client.source}"
        if client.source not in LIVE_SOURCES:
            logging.info("Skipping archived source %s", client.source)
            continue
        if not state.check_due(feed, config.external_store_interval, now):
            logging.info("External category %s is not due yet", client.source)
            continue
        try:
            products = checked_products(client.source, client.products(), state)
            state.retain_source_products(client.source, {product["key"] for product in products})
            observe_products(config, state, products, feed=feed)
            state.mark_succeeded(feed, now)
            if state.clear_feed_error(feed):
                send_store_status(
                    config.webhook_url,
                    "✅ Store scan recovered",
                    client.source,
                    "The store scan is working again.",
                    0x3BA55D,
                )
            logging.info("External category %s: %s products", client.source, len(products))
        except EXPECTED_NETWORK_ERRORS as error:
            logging.exception("External category %s is temporarily unavailable", client.source)
            if state.mark_feed_error(feed, f"{type(error).__name__}: {error}"):
                send_store_status(
                    config.webhook_url,
                    "⚠️ Store scan failed",
                    client.source,
                    f"The monitor could not scan this store. It will keep retrying every run.\n\n`{type(error).__name__}: {error}`",
                    0xF97316,
                )
        finally:
            state.mark_checked(feed, now)


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    config = Config.from_env()
    state = State(config.database_path)
    stopping = False

    def stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    while not stopping:
        check_once(config, state)
        if config.run_once:
            break
        for _ in range(config.poll_seconds):
            if stopping:
                break
            time.sleep(1)


if __name__ == "__main__":
    main()
