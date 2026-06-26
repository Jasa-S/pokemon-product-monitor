#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from monitor import State, USER_AGENT, load_watchlist


RATE_URL = "https://api.frankfurter.dev/v2/rate/KRW/EUR?providers=ECB"
ARCHIVED_SOURCE_PREFIXES = ("naver-",)


def previous_status(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as source:
            return json.load(source)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def previous_rate(path: str) -> dict | None:
    return previous_status(path).get("exchangeRate")


def fetch_exchange_rate(fallback_path: str = "docs/status.json") -> dict | None:
    cached = previous_rate(fallback_path)
    if cached and cached.get("date") == datetime.now(timezone.utc).date().isoformat():
        return cached
    try:
        request = Request(RATE_URL, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        with urlopen(request, timeout=15) as response:
            data = json.load(response)
        return {
            "base": "KRW",
            "quote": "EUR",
            "rate": float(data["rate"]),
            "date": data["date"],
            "source": "Frankfurter (ECB reference rate)",
        }
    except (HTTPError, URLError, TimeoutError, KeyError, ValueError, json.JSONDecodeError):
        return cached


def dashboard_updated_at(previous: dict, content: dict, now: str) -> str:
    unchanged = all(previous.get(key) == value for key, value in content.items())
    return previous.get("updatedAt", now) if unchanged else now


def dashboard_product(product: dict) -> bool:
    source = str(product.get("source", ""))
    return not source.startswith(ARCHIVED_SOURCE_PREFIXES)


def main() -> None:
    status_path = "docs/status.json"
    previous = previous_status(status_path)
    state = State(os.getenv("DATABASE_PATH", "state/monitor.db"))
    products = sorted(
        (product for product in state.all() if dashboard_product(product)),
        key=lambda item: (item["source"], item["productName"]),
    )
    content = {
        "exchangeRate": fetch_exchange_rate(status_path),
        "watchProductNos": sorted(str(value) for value in load_watchlist("watchlist.json")),
        "products": products,
    }
    now = datetime.now(timezone.utc).isoformat()
    payload = {"updatedAt": dashboard_updated_at(previous, content, now), **content}
    os.makedirs("docs", exist_ok=True)
    with open(status_path, "w", encoding="utf-8") as output:
        json.dump(payload, output, ensure_ascii=False, indent=2)
        output.write("\n")


if __name__ == "__main__":
    main()
