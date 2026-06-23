#!/usr/bin/env python3
"""User-controlled local Naver category monitor using persistent WebKit."""

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
STATE_PATH = ROOT / ".naver-local-monitor-state.json"
USER_AGENT = "PokemonStoreAvailabilityMonitor/2.0 (+personal-use)"
NAVER_CATEGORIES = (
    {
        "label": "Xoplay", "source": "naver-xoplay", "slug": "xoplay",
        "url": "https://smartstore.naver.com/xoplay/category/b6472710a7524259aae727a12b3495a3",
    },
    {
        "label": "Pokémon Brand cards", "source": "naver-pokemon", "slug": "pokemon",
        "url": "https://brand.naver.com/pokemon/category/c94139abcef14362997090c5da975e28",
    },
)


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


def high_resolution_naver_image(url: str | None) -> str | None:
    if not url:
        return None
    if "shop-phinf.pstatic.net" not in url:
        return url
    if re.search(r"([?&])type=f\d+_\d+", url):
        return re.sub(r"([?&])type=f\d+_\d+", r"\1type=f750_750", url)
    return f"{url}{'&' if '?' in url else '?'}type=f750_750"


def deduplicate_products(products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return list({product["key"]: product for product in products}.values())


def normalize_raw_product(
    raw: dict[str, Any], source: str = "naver-xoplay", slug: str = "xoplay"
) -> Optional[dict[str, Any]]:
    match = re.search(rf"/{re.escape(slug)}/products/(\d+)", raw.get("url", ""))
    if not match:
        return None
    text = re.sub(r"\s+", " ", raw.get("text", "")).strip()
    name = re.sub(r"\s+", " ", raw.get("name", "")).strip()
    if not name or name.casefold() in {slug.casefold(), "상품 이미지", "product image"}:
        lines = [
            line.strip() for line in raw.get("text", "").splitlines()
            if line.strip() and not re.fullmatch(r"[\d,]+\s*원?", line.strip())
            and "품절" not in line
        ]
        name = max(lines, key=len, default=f"Naver product {match.group(1)}")
    prices = [int(value.replace(",", "")) for value in re.findall(r"([\d,]+)\s*원", text)]
    sold_out = any(word in text.casefold() for word in ("품절", "sold out", "판매중지"))
    return {
        "key": f"{source}:{match.group(1)}", "source": source,
        "productNo": match.group(1), "productName": name,
        "salePrice": min(prices) if prices else None, "currency": "KRW",
        "image": high_resolution_naver_image(raw.get("image")), "isSoldOut": sold_out,
        "saleStatusType": "OUTOFSTOCK" if sold_out else "SALE",
        "stockStatus": "SOLD_OUT" if sold_out else "AVAILABLE",
        "url": raw["url"].split("?", 1)[0],
    }


def product_events(
    previous: dict[str, dict[str, Any]], products: list[dict[str, Any]]
) -> list[tuple[str, dict[str, Any]]]:
    if not previous:
        return []
    events = []
    for product in products:
        old = previous.get(product["key"])
        if old is None:
            events.append(("new", product))
        elif old.get("isSoldOut") and not product["isSoldOut"]:
            events.append(("restock", product))
    return events


def scan_events(
    previous: dict[str, dict[str, Any]], products: list[dict[str, Any]],
    previous_categories: set[str], current_categories: set[str],
) -> list[tuple[str, dict[str, Any]]]:
    if current_categories - previous_categories:
        logging.info("New category scope detected; establishing a silent baseline")
        return []
    return product_events(previous, products)


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
        url, data=json.dumps(data).encode() if data is not None else None,
        headers={
            "Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}",
            "Content-Type": "application/json", "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="PUT" if data is not None else "GET",
    )
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def newer_dashboard_state(
    local: dict[str, Any], remote: dict[str, Any], categories: set[str]
) -> dict[str, Any]:
    if not remote.get("products") or remote.get("updatedAt", "") <= local.get("updatedAt", ""):
        return local
    return {
        "updatedAt": remote["updatedAt"],
        "categories": sorted(categories),
        "products": remote["products"],
    }


def load_dashboard_state(token: str) -> dict[str, Any]:
    if not token or os.getenv("XOPLAY_GITHUB_SYNC", "true").casefold() != "true":
        return {}
    repository = os.getenv("GITHUB_REPOSITORY", "Jasa-S/pokemon-product-monitor")
    api_url = f"https://api.github.com/repos/{repository}/contents/docs/local-naver.json"
    try:
        current = github_request(api_url, token)
        return json.loads(base64.b64decode(current["content"]).decode())
    except (HTTPError, OSError, ValueError, KeyError, json.JSONDecodeError):
        logging.warning("Could not load the shared Naver baseline; local state retained")
        return {}


def sync_dashboard(products: list[dict[str, Any]], token: str) -> None:
    if os.getenv("XOPLAY_GITHUB_SYNC", "true").casefold() != "true":
        return
    if not token:
        logging.warning("GitHub authentication unavailable; dashboard sync skipped")
        return
    repository = os.getenv("GITHUB_REPOSITORY", "Jasa-S/pokemon-product-monitor")
    api_url = f"https://api.github.com/repos/{repository}/contents/docs/local-naver.json"
    payload = {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "products": sorted(products, key=lambda product: (product["source"], product["productName"])),
    }
    encoded = base64.b64encode(
        (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode()
    ).decode()
    for attempt in range(2):
        try:
            current = github_request(api_url, token)
            current_payload = json.loads(base64.b64decode(current["content"]).decode())
            if current_payload.get("products") == payload["products"]:
                return
            github_request(api_url, token, {
                "message": "Update local Naver catalogues", "content": encoded,
                "sha": current["sha"], "branch": "main",
            })
            logging.info("Published %s local Naver products to the dashboard", len(products))
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
            "gh", "workflow", "run", "naver-notify.yml", "--repo", repository,
            "-f", f"event={event}",
            "-f", f"product_json={json.dumps(product, ensure_ascii=False)}",
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
    needs_login, needs_captcha = login_present(page), captcha_present(page)
    if not needs_login and not needs_captcha:
        return True
    if needs_login:
        logging.warning("Naver login required. Log in yourself in the open WebKit window.")
    if needs_captcha:
        logging.warning("Naver needs verification. Complete the CAPTCHA in the open window.")
    while not should_stop() and (login_present(page) or captcha_present(page)):
        time.sleep(2)
    if should_stop():
        return False
    logging.info("Naver access completed; continuing")
    return True


def collect_products_from_page(
    page: Any, category: dict[str, str]
) -> list[dict[str, Any]]:
    """Extract all product links currently visible on the page."""
    slug = category["slug"]
    selector = f'a[href*="/{slug}/products/"]'
    try:
        page.wait_for_selector(selector, timeout=8000)
    except Exception:
        return []
    page.wait_for_timeout(1000)
    raw_products = page.locator(selector).evaluate_all("""
        links => links.map(link => {
          const card = link.closest('li') || link.closest('article') || link.parentElement?.parentElement || link;
          const image = card.querySelector('img') || link.querySelector('img');
          return {url: link.href, text: card.innerText || link.innerText || '',
            name: image?.alt || link.getAttribute('aria-label') || '', image: image?.src || ''};
        })
    """)
    found = {}
    for raw in raw_products:
        product = normalize_raw_product(raw, category["source"], slug)
        if product:
            found[product["productNo"]] = product
    return list(found.values())


def scrape_catalog_by_url(
    page: Any, category: dict[str, str], max_pages: int,
    should_stop: Callable[[], bool] = lambda: False,
) -> list[dict[str, Any]]:
    """Paginate by building ?page=N URLs — works for smartstore.naver.com."""
    found = {}
    for page_number in range(1, max_pages + 1):
        params = urlencode({"st": "RECENT", "dt": "IMAGE", "page": page_number, "size": 80})
        page.goto(f"{category['url']}?{params}", wait_until="domcontentloaded")
        if not wait_for_access(page, should_stop):
            break
        products = collect_products_from_page(page, category)
        new_count = sum(p["productNo"] not in found for p in products)
        found.update({p["productNo"]: p for p in products})
        logging.info("%s page %s: %s products (%s new)", category["label"], page_number, len(products), new_count)
        if not products or (page_number > 1 and new_count == 0):
            break
    return list(found.values())


def scrape_catalog_by_click(
    page: Any, category: dict[str, str], max_pages: int,
    should_stop: Callable[[], bool] = lambda: False,
) -> list[dict[str, Any]]:
    """Paginate by clicking numbered page buttons — required for brand.naver.com SPA.
    Buttons use data-shp-area-id='pgn' and data-shp-contents-id='{page_number}'.
    """
    params = urlencode({"st": "RECENT", "dt": "IMAGE", "page": 1, "size": 80})
    page.goto(f"{category['url']}?{params}", wait_until="domcontentloaded")
    if not wait_for_access(page, should_stop):
        return []

    found = {}
    page_number = 1

    while page_number <= max_pages and not should_stop():
        products = collect_products_from_page(page, category)
        new_count = sum(p["productNo"] not in found for p in products)
        found.update({p["productNo"]: p for p in products})
        logging.info("%s page %s: %s products (%s new)", category["label"], page_number, len(products), new_count)

        if not products:
            break

        next_page = page_number + 1
        # Naver brand store page buttons: <a data-shp-area-id="pgn" data-shp-contents-id="2">2</a>
        next_btn = page.locator(f'a[data-shp-area-id="pgn"][data-shp-contents-id="{next_page}"]').first
        try:
            next_btn.wait_for(state="visible", timeout=3000)
        except Exception:
            # No button for the next page — we are on the last page
            break
        next_btn.click()
        # Wait for the SPA to re-render the product list
        page.wait_for_timeout(2500)
        page_number += 1

    return list(found.values())


def scrape_catalog(
    page: Any, category: dict[str, str], max_pages: int,
    should_stop: Callable[[], bool] = lambda: False,
) -> list[dict[str, Any]]:
    """Route to click-based or URL-based pagination depending on the store domain."""
    if "brand.naver.com" in category["url"]:
        return scrape_catalog_by_click(page, category, max_pages, should_stop)
    return scrape_catalog_by_url(page, category, max_pages, should_stop)


def run() -> None:
    from playwright.sync_api import sync_playwright

    poll_seconds = max(60, int(os.getenv("XOPLAY_POLL_SECONDS", "300")))
    max_pages = max(1, int(os.getenv("XOPLAY_MAX_PAGES", "20")))
    run_once = os.getenv("XOPLAY_RUN_ONCE", "false").casefold() == "true"
    headless = os.getenv("XOPLAY_HEADLESS", "false").casefold() == "true"
    browser_name = os.getenv("XOPLAY_BROWSER", "webkit").casefold()
    if browser_name not in {"webkit", "chromium"}:
        raise ValueError("XOPLAY_BROWSER must be webkit or chromium")
    stopping = False

    def stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    token = gh_token()
    previous_state = load_json(STATE_PATH, {})
    current_categories = {category["url"].rsplit("/", 1)[-1] for category in NAVER_CATEGORIES}
    shared_state = newer_dashboard_state(
        previous_state, load_dashboard_state(token), current_categories
    )
    if shared_state is not previous_state:
        previous_state = shared_state
        save_json(STATE_PATH, previous_state)
        logging.info("Resumed from the newer shared Naver baseline")
    previous_list = previous_state.get("products", [])
    previous_categories = set(previous_state.get("categories", []))
    with sync_playwright() as playwright:
        profile = ROOT / f".naver-{browser_name}-profile"
        context = getattr(playwright, browser_name).launch_persistent_context(
            str(profile), headless=headless, locale="ko-KR",
            viewport={"width": 1280, "height": 900},
        )
        page = context.pages[0] if context.pages else context.new_page()
        while not stopping:
            previous = {product["key"]: product for product in previous_list}
            combined = []
            for category in NAVER_CATEGORIES:
                try:
                    products = scrape_catalog(page, category, max_pages, lambda: stopping)
                    if products:
                        combined.extend(products)
                    else:
                        combined.extend(
                            product for product in previous_list
                            if product.get("source") == category["source"]
                        )
                        logging.warning("No %s products found; previous state retained", category["label"])
                except Exception:
                    logging.exception("%s check failed; previous state retained", category["label"])
                    if page.is_closed():
                        logging.error("Browser window was closed; stopping the local monitor")
                        stopping = True
                        break
                    combined.extend(
                        product for product in previous_list
                        if product.get("source") == category["source"]
                    )
            combined = deduplicate_products(combined)
            if stopping:
                break
            if combined:
                events = scan_events(
                    previous, combined, previous_categories, current_categories
                )
                previous_list = combined
                save_json(STATE_PATH, {
                    "updatedAt": datetime.now(timezone.utc).isoformat(),
                    "categories": sorted(current_categories),
                    "products": combined,
                })
                sync_dashboard(combined, token)
                previous_categories = current_categories
                logging.info("Local Naver check complete: %s products", len(combined))
                for event, product in events:
                    dispatch_alert(event, product)
            if run_once:
                break
            deadline = time.monotonic() + poll_seconds
            while not stopping and time.monotonic() < deadline:
                time.sleep(1)
        if not page.is_closed():
            context.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run()
