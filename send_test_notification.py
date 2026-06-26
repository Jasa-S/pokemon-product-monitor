#!/usr/bin/env python3
import os

from monitor import send_discord


webhook = os.environ["DISCORD_WEBHOOK_URL"]
sample = {
    "key": "crazycards-pokemon:test-card",
    "source": "crazycards-pokemon",
    "productNo": "test-card",
    "productName": "Test Pokémon card product",
    "salePrice": 9.99,
    "currency": "EUR",
    "image": None,
    "isSoldOut": False,
    "saleStatusType": "SALE",
    "stockStatus": "AVAILABLE",
    "url": "https://www.crazycards.eu/pokemon",
}
send_discord(webhook, "🧪 EU monitor test notification", sample, 0x5865F2)
