#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from monitor import State, USER_AGENT


RATE_URL = "https://api.frankfurter.dev/v2/rate/KRW/EUR?providers=ECB"


def previous_rate(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as source:
            return json.load(source).get("exchangeRate")
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def fetch_exchange_rate(fallback_path: str = "docs/status.json") -> dict | None:
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
        return previous_rate(fallback_path)


def main() -> None:
    status_path = "docs/status.json"
    state = State(os.getenv("DATABASE_PATH", "state/monitor.db"))
    products = sorted(state.all(), key=lambda item: (item["source"], item["productName"]))
    payload = {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "exchangeRate": fetch_exchange_rate(status_path),
        "products": products,
    }
    os.makedirs("docs", exist_ok=True)
    with open(status_path, "w", encoding="utf-8") as output:
        json.dump(payload, output, ensure_ascii=False, indent=2)
        output.write("\n")


if __name__ == "__main__":
    main()
