#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from monitor import LIVE_SOURCES, State, is_available


def dashboard_product(product: dict) -> bool:
    return str(product.get("source", "")) in LIVE_SOURCES


def iso_from_timestamp(value: float | None) -> str | None:
    return datetime.fromtimestamp(value, timezone.utc).isoformat() if value is not None else None


def feed_checked_at(state: State, feed: str) -> str | None:
    row = state.db.execute("SELECT checked_at FROM check_times WHERE feed = ?", (feed,)).fetchone()
    return iso_from_timestamp(float(row[0])) if row else None


def feed_error(state: State, feed: str) -> dict[str, Any]:
    row = state.db.execute(
        "SELECT failing, message, updated_at FROM feed_errors WHERE feed = ?", (feed,)
    ).fetchone()
    if not row:
        return {"failing": False, "message": None, "updatedAt": None}
    return {
        "failing": bool(row[0]),
        "message": row[1],
        "updatedAt": iso_from_timestamp(float(row[2])) if row[2] is not None else None,
    }


def source_summaries(state: State, products: list[dict]) -> list[dict[str, Any]]:
    summaries = []
    for source in sorted(LIVE_SOURCES):
        source_products = [product for product in products if product.get("source") == source]
        feed = f"external:{source}"
        error = feed_error(state, feed)
        summaries.append({
            "source": source,
            "productCount": len(source_products),
            "availableCount": sum(is_available(product) for product in source_products),
            "soldOutCount": sum(bool(product.get("isSoldOut")) for product in source_products),
            "checkedAt": feed_checked_at(state, feed),
            "failing": error["failing"],
            "errorMessage": error["message"] if error["failing"] else None,
            "errorUpdatedAt": error["updatedAt"] if error["failing"] else None,
        })
    return summaries


def main() -> None:
    status_path = "docs/status.json"
    state = State(os.getenv("DATABASE_PATH", "state/monitor.db"))
    products = sorted(
        (product for product in state.all() if dashboard_product(product)),
        key=lambda item: (item["source"], item["productName"]),
    )
    payload = {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "sources": source_summaries(state, products),
        "products": products,
    }
    os.makedirs("docs", exist_ok=True)
    with open(status_path, "w", encoding="utf-8") as output:
        json.dump(payload, output, ensure_ascii=False, indent=2)
        output.write("\n")


if __name__ == "__main__":
    main()
