#!/usr/bin/env python3
import json
import os
from datetime import datetime, timezone

from monitor import State


state = State(os.getenv("DATABASE_PATH", "state/monitor.db"))
products = sorted(state.all(), key=lambda item: (item["source"], item["productName"]))
payload = {
    "updatedAt": datetime.now(timezone.utc).isoformat(),
    "products": products,
}
os.makedirs("docs", exist_ok=True)
with open("docs/status.json", "w", encoding="utf-8") as output:
    json.dump(payload, output, ensure_ascii=False, indent=2)
    output.write("\n")
