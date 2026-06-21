#!/usr/bin/env python3
import os

from monitor import normalized_product, send_discord


webhook = os.environ["DISCORD_WEBHOOK_URL"]
sample = normalized_product(
    source="pokemonstore",
    product_id=133526939,
    name="포켓몬 스토어 「포켓몬 런」 빅사이즈 피카츄 봉제인형",
    price=84000,
    image="https://shopby-images.cdn-nhncommerce.com/Mall-No-h6Ss/20260507/211526.397653913/KakaoTalk_20260504_114904623_01.png",
    available=True,
    status="ONSALE",
    url="https://www.pokemonstore.co.kr/pages/product/product-detail.html?productNo=133526939",
)
send_discord(webhook, "🧪 Monitor test notification", sample, 0x5865F2)
