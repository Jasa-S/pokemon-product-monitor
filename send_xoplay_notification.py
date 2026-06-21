#!/usr/bin/env python3
import json
import os

from monitor import send_discord


event = os.environ["XOPLAY_EVENT"]
product = json.loads(os.environ["XOPLAY_PRODUCT_JSON"])
title = "✨ New Xoplay product" if event == "new" else "✅ Xoplay product back in stock"
color = 0xFFCB05 if event == "new" else 0x3BA55D
send_discord(os.environ.get("DISCORD_WEBHOOK_URL", ""), title, product, color)
