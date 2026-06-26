#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from monitor import LIVE_SOURCES, State


def previous_status(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as source:
            return json.load(source)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def dashboard_updated_at(previous: dict, content: dict, now: str) -> str:
    unchanged = all(previous.get(key) == value for key, value in content.items())
    return previous.get("updatedAt", now) if unchanged else now


def dashboard_product(product: dict) -> bool:
    return str(product.get("source", "")) in LIVE_SOURCES


def main() -> None:
    status_path = "docs/status.json"
    previous = previous_status(status_path)
    state = State(os.getenv("DATABASE_PATH", "state/monitor.db"))
    products = sorted(
        (product for product in state.all() if dashboard_product(product)),
        key=lambda item: (item["source"], item["productName"]),
    )
    content = {"products": products}
    now = datetime.now(timezone.utc).isoformat()
    payload = {"updatedAt": dashboard_updated_at(previous, content, now), **content}
    os.makedirs("docs", exist_ok=True)
    with open(status_path, "w", encoding="utf-8") as output:
        json.dump(payload, output, ensure_ascii=False, indent=2)
        output.write("\n")


if __name__ == "__main__":
    main()
