#!/usr/bin/env python3
"""Send a daily Discord health report for the TCG monitor."""

from __future__ import annotations

import argparse
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from export_dashboard import dashboard_product, source_summaries
from monitor import State, send_discord_payload


REPORT_TZ = ZoneInfo("Europe/Berlin")
REPORT_HOUR = 18


def local_now() -> datetime:
    return datetime.now(REPORT_TZ)


def should_send(now: datetime, force: bool) -> bool:
    return force or now.hour == REPORT_HOUR


def report_key(now: datetime) -> str:
    return now.strftime("%Y-%m-%d")


def ensure_report_table(state: State) -> None:
    state.db.execute(
        "CREATE TABLE IF NOT EXISTS daily_reports "
        "(report_date TEXT PRIMARY KEY, sent_at TEXT NOT NULL)"
    )
    state.db.commit()


def already_sent(state: State, key: str) -> bool:
    ensure_report_table(state)
    return state.db.execute(
        "SELECT 1 FROM daily_reports WHERE report_date = ?", (key,)
    ).fetchone() is not None


def mark_sent(state: State, key: str, now: datetime) -> None:
    ensure_report_table(state)
    state.db.execute(
        "INSERT OR REPLACE INTO daily_reports(report_date, sent_at) VALUES(?, ?)",
        (key, now.isoformat()),
    )
    state.db.commit()


def live_products(state: State) -> list[dict]:
    return [product for product in state.all() if dashboard_product(product)]


def build_report(state: State, now: datetime) -> dict:
    products = live_products(state)
    sources = source_summaries(state, products)
    failing = [source for source in sources if source.get("failing")]
    total = sum(int(source.get("productCount") or 0) for source in sources)
    available = sum(int(source.get("availableCount") or 0) for source in sources)
    lines = []
    for source in sources:
        status = "FAILING" if source.get("failing") else "OK"
        checked_at = source.get("checkedAt") or "never"
        line = (
            f"{status} · {source['source']} · "
            f"{source.get('productCount', 0)} products · "
            f"{source.get('availableCount', 0)} in stock · "
            f"last scan {checked_at}"
        )
        if source.get("failing") and source.get("errorMessage"):
            line += f" · {source['errorMessage']}"
        lines.append(line)
    color = 0xF97316 if failing else 0x3BA55D
    return {
        "embeds": [
            {
                "title": "Daily TCG Monitor Report",
                "description": "```\n" + "\n".join(lines)[:3800] + "\n```",
                "color": color,
                "fields": [
                    {"name": "Products", "value": str(total), "inline": True},
                    {"name": "In stock", "value": str(available), "inline": True},
                    {"name": "Failing stores", "value": str(len(failing)), "inline": True},
                ],
                "footer": {"text": f"Generated {now.isoformat()} Europe/Berlin"},
            }
        ]
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    now = local_now()
    force = args.force or os.getenv("FORCE_DAILY_REPORT", "false").casefold() == "true"
    if not should_send(now, force):
        print(f"Not report time in Europe/Berlin: {now.isoformat()}")
        return
    state = State(os.getenv("DATABASE_PATH", "state/monitor.db"))
    key = report_key(now)
    if not force and already_sent(state, key):
        print(f"Daily report already sent for {key}")
        return
    sent = send_discord_payload(os.environ["DISCORD_WEBHOOK_URL"], build_report(state, now))
    if not sent:
        raise RuntimeError("Daily Discord report was not delivered")
    mark_sent(state, key, now)
    print(f"Daily report sent for {key}")


if __name__ == "__main__":
    main()
