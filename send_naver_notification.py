#!/usr/bin/env python3
import json
import os

from monitor import send_discord


event = os.environ["NAVER_EVENT"]
product = json.loads(os.environ["NAVER_PRODUCT_JSON"])
store = "Xoplay" if product["source"] == "naver-xoplay" else "Naver Pokémon"
title = f"✨ New {store} product" if event == "new" else f"✅ {store} product back in stock"
color = 0xFFCB05 if event == "new" else 0x3BA55D
send_discord(os.environ.get("DISCORD_WEBHOOK_URL", ""), title, product, color)
