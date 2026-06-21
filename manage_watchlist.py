#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import sys

from monitor import State, load_watchlist


def update_watchlist(title: str, path: str = "watchlist.json", database_path: str = "state/monitor.db") -> str:
    match = re.fullmatch(r"\[(watch|unwatch)]\s+(\d+)", title.strip(), re.IGNORECASE)
    if not match:
        raise ValueError("Issue title must be '[watch] PRODUCT_NO' or '[unwatch] PRODUCT_NO'")
    action, value = match.group(1).lower(), int(match.group(2))
    state = State(database_path)
    if state.get(f"pokemonstore:{value}") is None:
        raise ValueError(f"Unknown Pokémon Store product number: {value}")

    products = load_watchlist(path)
    if action == "watch":
        products.add(value)
    else:
        products.discard(value)
    with open(path, "w", encoding="utf-8") as output:
        json.dump({"productNos": sorted(products)}, output, indent=2)
        output.write("\n")
    return f"{action}ed product {value}"


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: manage_watchlist.py '[watch] PRODUCT_NO'")
    print(update_watchlist(sys.argv[1], database_path=os.getenv("DATABASE_PATH", "state/monitor.db")))
