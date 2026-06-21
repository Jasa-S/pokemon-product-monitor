#!/usr/bin/env python3
"""User-controlled local Xoplay monitor using a persistent visible browser."""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import signal
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
CATEGORY_ID = "b6472710a7524259aae727a12b3495a3"
CATEGORY_URL = f"https://smartstore.naver.com/xoplay/category/{CATEGORY_ID}"
STATE_PATH = ROOT / ".xoplay-monitor-state.json"
PROFILE_PATH = ROOT / ".xoplay-browser"
USER_AGENT = "PokemonStoreAvailabilityMonitor/2.0 (+personal-use)"


def load_json(path: Path, fallback: Any) -> Any:
    try:
        with path.open(encoding="utf-8") as source:
            return json.load(source)
    except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
        return fallback


def save_json(path: Path, payload: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        json.dump(payload, output, ensure_ascii=False, indent=2)
        output.write("\n")
    temporary.replace(path)


def normalize_raw_product(raw: dict[str, Any]) -> Optional[dict[str, Any]]:
    match = re.search(r"/xoplay/products/(\d+)", raw.get("url", ""))
    if not match:
        return None
    text = re.sub(r"\s+", " ", raw.get("text", "")).strip()
    name = re.sub(r"\s+", " ", raw.get("name", "")).strip()
    if not name or name.casefold() in {"xoplay", "상품 이미지", "product image"}:
        lines = [
            line.strip() for line in raw.get("text", "").splitlines()
            if line.strip() and not re.fullmatch(r"[\d,]+\s*원?", line.strip())
            and "품절" not in line
        ]
        name = max(lines, key=len, default=f"Xoplay product {match.group(1)}")
    prices = [int(value.replace(",", "")) for value in re.findall(r"([\d,]+)\s*원", text)]
    sold_out = any(word in text.casefold() for word in ("품절", "sold out", "판매중지"))
    return {
        "key": f"naver-xoplay:{match.group(1)}",
        "source": "naver-xoplay",
        "productNo": match.group(1),
        "productName": name,
        "salePrice": min(prices) if prices else None,
        "image": raw.get("image") or None,
        "isSoldOut": sold_out,
        "saleStatusType": "OUTOFSTOCK" if sold_out else "SALE",
        "stockStatus": "SOLD_OUT" if sold_out else "AVAILABLE",
        "url": f"https://smartstore.naver.com/xoplay/products/{match.group(1)}",
    }


def product_events(previous: dict[str, dict[str, Any]], products: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    if not previous:
        return []
    events: list[tuple[str, dict[str, Any]]] = []
    for product in products:
        old = previous.get(product["key"])
        if old is None:
            events.append(("new", product))
        elif old.get("isSoldOut") and not product["isSoldOut"]:
            events.append(("restock", product))
    return events


def gh_token() -> str:
    token = os.getenv("GH_TOKEN", "").strip()
    if token:
        return token
    try:
        return subprocess.check_output(
            ["gh", "auth", "token"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return ""


def github_request(
    url: str, token: str, data: Optional[dict[str, Any]] = None
) -> dict[str, Any]:
    request = Request(
        url,
        data=json.dumps(data).encode() if data is not None else None,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="PUT" if data is not None else "GET",
    )
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def sync_dashboard(products: list[dict[str, Any]], token: str) -> None:
    if os.getenv("XOPLAY_GITHUB_SYNC", "true").casefold() != "true":
        return
    if not token:
        logging.warning("GitHub authentication unavailable; dashboard sync skipped")
        return
    repository = os.getenv("GITHUB_REPOSITORY", "Jasa-S/pokemon-product-monitor")
    api_url = f"https://api.github.com/repos/{repository}/contents/docs/xoplay.json"
    payload = {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "products": sorted(products, key=lambda product: product["productName"]),
    }
    encoded = base64.b64encode((json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode()).decode()
    for attempt in range(2):
        try:
            current = github_request(api_url, token)
            current_payload = json.loads(base64.b64decode(current["content"]).decode())
            if current_payload.get("products") == payload["products"]:
                return
            github_request(api_url, token, {
                "message": "Update local Xoplay catalogue",
                "content": encoded,
                "sha": current["sha"],
                "branch": "main",
            })
            logging.info("Published %s Xoplay products to the dashboard", len(products))
            return
        except HTTPError as error:
            if error.code == 409 and attempt == 0:
                time.sleep(2)
                continue
            logging.warning("Dashboard sync failed with HTTP %s", error.code)
            return
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            logging.warning("Dashboard sync failed", exc_info=True)
            return


def dispatch_alert(event: str, product: dict[str, Any]) -> None:
    repository = os.getenv("GITHUB_REPOSITORY", "Jasa-S/pokemon-product-monitor")
    try:
        subprocess.run([
            "gh", "workflow", "run", "xoplay-notify.yml", "--repo", repository,
            "-f", f"event={event}", "-f", f"product_json={json.dumps(product, ensure_ascii=False)}",
        ], check=True, stdout=subprocess.DEVNULL)
        logging.info("Queued %s Discord alert for %s", event, product["productName"])
    except (FileNotFoundError, subprocess.CalledProcessError):
        logging.warning("Could not queue Discord alert; check `gh auth status`")


def captcha_present(page: Any) -> bool:
    content = page.locator("body").inner_text().casefold()
    return "security verification" in content or "captcha" in content or "보안 확인" in content


def login_present(page: Any) -> bool:
    return "nid.naver.com/nidlogin" in page.url


def wait_for_access(page: Any, should_stop: Callable[[], bool]) -> bool:
    needs_login = login_present(page)
    needs_captcha = captcha_present(page)
    if not needs_login and not needs_captcha:
        return True
    if needs_login:
        logging.warning("Naver login required. Log in yourself in the open browser window.")
    if needs_captcha:
        logging.warning("Naver needs verification. Complete the CAPTCHA in the open browser window.")
    while not should_stop() and (login_present(page) or captcha_present(page)):
        time.sleep(2)
    if should_stop():
        return False
    logging.info("Naver access completed; continuing")
    return True


def scrape_page(
    page: Any, page_number: int, should_stop: Callable[[], bool] = lambda: False
) -> list[dict[str, Any]]:
    page.goto(f"{CATEGORY_URL}?{urlencode({'cp': page_number})}", wait_until="domcontentloaded")
    if not wait_for_access(page, should_stop):
        return []
    page.wait_for_timeout(1500)
    raw_products = page.locator('a[href*="/xoplay/products/"]').evaluate_all("""
        links => links.map(link => {
          const card = link.closest('li') || link.closest('article') || link.parentElement?.parentElement || link;
          const image = card.querySelector('img') || link.querySelector('img');
          return {
            url: link.href,
            text: card.innerText || link.innerText || '',
            name: image?.alt || link.getAttribute('aria-label') || '',
            image: image?.src || ''
          };
        })
    """)
    found: dict[str, dict[str, Any]] = {}
    for raw in raw_products:
        product = normalize_raw_product(raw)
        if product:
            found[product["productNo"]] = product
    return list(found.values())


def scrape_catalog(
    page: Any, max_pages: int, should_stop: Callable[[], bool] = lambda: False
) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for page_number in range(1, max_pages + 1):
        products = scrape_page(page, page_number, should_stop)
        new_count = sum(product["productNo"] not in found for product in products)
        for product in products:
            found[product["productNo"]] = product
        logging.info("Xoplay page %s: %s products (%s new)", page_number, len(products), new_count)
        if not products or new_count == 0:
            break
    return list(found.values())


def run() -> None:
    from playwright.sync_api import sync_playwright

    poll_seconds = max(60, int(os.getenv("XOPLAY_POLL_SECONDS", "300")))
    max_pages = max(1, int(os.getenv("XOPLAY_MAX_PAGES", "20")))
    run_once = os.getenv("XOPLAY_RUN_ONCE", "false").casefold() == "true"
    headless = os.getenv("XOPLAY_HEADLESS", "false").casefold() == "true"
    stopping = False

    def stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    token = gh_token()
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(PROFILE_PATH), headless=headless, locale="ko-KR",
            viewport={"width": 1280, "height": 900},
        )
        page = context.pages[0] if context.pages else context.new_page()
        while not stopping:
            try:
                products = scrape_catalog(page, max_pages, lambda: stopping)
                if not products:
                    logging.warning("No Xoplay products found; previous state retained")
                else:
                    previous_list = load_json(STATE_PATH, {}).get("products", [])
                    previous = {product["key"]: product for product in previous_list}
                    for event, product in product_events(previous, products):
                        dispatch_alert(event, product)
                    save_json(STATE_PATH, {
                        "updatedAt": datetime.now(timezone.utc).isoformat(), "products": products,
                    })
                    sync_dashboard(products, token)
                    logging.info("Xoplay check complete: %s products", len(products))
            except Exception:
                logging.exception("Xoplay check failed; previous state retained")
            if run_once:
                break
            deadline = time.monotonic() + poll_seconds
            while not stopping and time.monotonic() < deadline:
                time.sleep(1)
        context.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run()
