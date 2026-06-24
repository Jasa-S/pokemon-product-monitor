#!/usr/bin/env python3
"""User-controlled local Naver category monitor using persistent Chromium."""

from __future__ import annotations

import base64
import json
import logging
import os
import random
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

# Realistic Windows Chrome user-agent to avoid Playwright automation fingerprint
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

# Script injected into every page to hide the navigator.webdriver automation flag
STEALTH_SCRIPT = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"

NAVER_CATEGORIES = (
    {
        "label": "Pok\u00e9mon Brand cards", "source": "naver-pokemon", "slug": "pokemon",
        "url": "https://brand.naver.com/pokemon/category/c94139abcef14362997090c5da975e28",
    },
    {
        "label": "Xoplay", "source": "naver-xoplay", "slug": "xoplay",
        "url": "https://smartstore.naver.com/xoplay/category/b6472710a7524259aae727a12b3495a3",
    },
)

# Pause between scraping each category so Naver doesn't return empty pages
CATEGORY_COOLDOWN_SECONDS = 3
# How long to wait for the user to solve a CAPTCHA before giving up and skipping
CAPTCHA_TIMEOUT_SECONDS = 300  # 5 minutes
# After a CAPTCHA clears, navigate away and wait this long before retrying
POST_CAPTCHA_COOLDOWN_SECONDS = 45
# If a category returns 0 products but previously had some, wait then retry once
EMPTY_RESULT_RETRY_SECONDS = 60


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
    if not name or name.casefold() in {slug.casefold(), "\uc0c1\ud488 \uc774\ubbf8\uc9c0", "product image"}:
        lines = [
            line.strip() for line in raw.get("text", "").splitlines()
            if line.strip() and not re.fullmatch(r"[\d,]+\s*\uc6d0?", line.strip())
            and "\ud488\uc808" not in line
        ]
        name = max(lines, key=len, default=f"Naver product {match.group(1)}")
    prices = [int(value.replace(",", "")) for value in re.findall(r"([\d,]+)\s*\uc6d0", text)]
    sold_out = any(word in text.casefold() for word in ("\ud488\uc808", "sold out", "\ud310\ub9e4\uc911\uc9c0"))
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


def dispatch_captcha_alert(category_label: str) -> None:
    """Send a Discord alert directly via webhook when a CAPTCHA is detected."""
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook_url:
        env_file = ROOT / ".env.xoplay"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if line.startswith("DISCORD_WEBHOOK_URL="):
                    webhook_url = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    if not webhook_url:
        logging.warning("DISCORD_WEBHOOK_URL not set; CAPTCHA Discord alert skipped")
        return
    payload = json.dumps({
        "embeds": [{
            "title": "\u26a0\ufe0f Naver CAPTCHA \u2014 action required",
            "description": (
                f"The monitor hit a CAPTCHA for **{category_label}** "
                f"and could not resolve it within {CAPTCHA_TIMEOUT_SECONDS // 60} minutes.\n\n"
                "Open the Chromium window on your PC and complete the verification "
                "so the next scan can proceed normally."
            ),
            "color": 0xFF6B35,
        }]
    }).encode()
    try:
        req = Request(
            webhook_url, data=payload,
            headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
            method="POST",
        )
        with urlopen(req, timeout=10):
            pass
        logging.info("Sent CAPTCHA Discord alert for %s", category_label)
    except (HTTPError, OSError) as exc:
        logging.warning("CAPTCHA Discord alert failed: %s", exc)


def captcha_present(page: Any) -> bool:
    try:
        content = page.locator("body").inner_text(timeout=3000).casefold()
    except Exception:
        return False
    return "security verification" in content or "captcha" in content or "\ubcf4\uc548 \ud655\uc778" in content


def login_present(page: Any) -> bool:
    return "nid.naver.com/nidlogin" in page.url


def wait_for_access(
    page: Any, should_stop: Callable[[], bool], category_label: str = ""
) -> bool:
    """Wait for CAPTCHA/login to clear, then cool down on the Naver homepage."""
    needs_login, needs_captcha = login_present(page), captcha_present(page)
    if not needs_login and not needs_captcha:
        return True
    if needs_login:
        logging.warning("Naver login required. Log in yourself in the open Chromium window.")
    if needs_captcha:
        logging.warning(
            "Naver CAPTCHA detected for %s. Complete it in the open Chromium window. "
            "Will skip after %s seconds if not resolved.",
            category_label, CAPTCHA_TIMEOUT_SECONDS,
        )
        dispatch_captcha_alert(category_label)
    deadline = time.monotonic() + CAPTCHA_TIMEOUT_SECONDS
    while not should_stop() and (login_present(page) or captcha_present(page)):
        if time.monotonic() > deadline:
            logging.warning(
                "CAPTCHA not resolved within %ss for %s; skipping this cycle.",
                CAPTCHA_TIMEOUT_SECONDS, category_label,
            )
            return False
        time.sleep(2)
    if should_stop():
        return False
    logging.info(
        "CAPTCHA cleared for %s; cooling down %ss on Naver homepage before retrying.",
        category_label, POST_CAPTCHA_COOLDOWN_SECONDS,
    )
    try:
        page.goto("https://www.naver.com", wait_until="domcontentloaded", timeout=15000)
    except Exception:
        pass
    deadline = time.monotonic() + POST_CAPTCHA_COOLDOWN_SECONDS
    while not should_stop() and time.monotonic() < deadline:
        time.sleep(1)
    if should_stop():
        return False
    logging.info("Post-CAPTCHA cooldown complete; continuing scrape for %s.", category_label)
    return True


def human_delay(min_s: float = 1.0, max_s: float = 3.0) -> None:
    time.sleep(random.uniform(min_s, max_s))


def collect_products_from_page(
    page: Any, category: dict[str, str],
    should_stop: Callable[[], bool] = lambda: False,
) -> Optional[list[dict[str, Any]]]:
    """Extract product links from the current page.
    Returns None on unresolved CAPTCHA, [] on genuinely empty page.
    """
    slug = category["slug"]
    selector = f'a[href*="/{slug}/products/"]'

    if captcha_present(page) or login_present(page):
        logging.warning("Mid-scrape CAPTCHA/login detected on %s", category["label"])
        if not wait_for_access(page, should_stop, category["label"]):
            return None

    try:
        page.wait_for_selector(selector, timeout=8000)
    except Exception:
        if captcha_present(page) or login_present(page):
            logging.warning("CAPTCHA appeared while waiting for products on %s", category["label"])
            if not wait_for_access(page, should_stop, category["label"]):
                return None
        return []

    page.evaluate("window.scrollTo({top: document.body.scrollHeight / 2, behavior: 'smooth'})")
    page.wait_for_timeout(600)
    page.evaluate("window.scrollTo({top: document.body.scrollHeight, behavior: 'smooth'})")
    page.wait_for_timeout(800)
    page.evaluate("window.scrollTo({top: 0, behavior: 'smooth'})")
    page.wait_for_timeout(400)

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


def scrape_catalog(
    page: Any, category: dict[str, str], max_pages: int,
    should_stop: Callable[[], bool] = lambda: False,
) -> list[dict[str, Any]]:
    """Paginate through category pages with human-like delays."""
    params = urlencode({"st": "RECENT", "dt": "IMAGE", "page": 1, "size": 80})
    page.goto(f"{category['url']}?{params}", wait_until="domcontentloaded")
    if not wait_for_access(page, should_stop, category["label"]):
        return []

    found = {}
    page_number = 1

    while page_number <= max_pages and not should_stop():
        products = collect_products_from_page(page, category, should_stop)

        if products is None:
            logging.warning("%s scrape aborted due to unresolved CAPTCHA", category["label"])
            return []

        new_count = sum(p["productNo"] not in found for p in products)
        found.update({p["productNo"]: p for p in products})
        logging.info(
            "%s page %s: %s products (%s new)",
            category["label"], page_number, len(products), new_count,
        )

        if not products:
            break

        next_page = page_number + 1
        next_btn = page.locator(
            f'a[data-shp-area-id="pgn"][data-shp-contents-id="{next_page}"]'
        ).first
        try:
            next_btn.wait_for(state="visible", timeout=3000)
        except Exception:
            break

        human_delay(1.0, 3.0)
        next_btn.click()
        page.wait_for_timeout(2500)
        human_delay(0.5, 1.5)

        if captcha_present(page) or login_present(page):
            logging.warning("CAPTCHA appeared after page click on %s page %s", category["label"], next_page)
            if not wait_for_access(page, should_stop, category["label"]):
                logging.warning("%s scrape aborted; returning %s products so far", category["label"], len(found))
                return list(found.values())
            page.goto(
                f"{category['url']}?{urlencode({'st': 'RECENT', 'dt': 'IMAGE', 'page': next_page, 'size': 80})}",
                wait_until="domcontentloaded",
            )

        page_number += 1

    return list(found.values())


def scrape_with_retry(
    page: Any, category: dict[str, str], max_pages: int,
    previous_list: list[dict[str, Any]],
    should_stop: Callable[[], bool] = lambda: False,
) -> tuple[list[dict[str, Any]], bool]:
    """Run scrape_catalog; if 0 products and cache exists, wait and retry once."""
    cached = [p for p in previous_list if p.get("source") == category["source"]]
    products = scrape_catalog(page, category, max_pages, should_stop)

    if products or should_stop():
        return products, False

    if not cached:
        return [], False

    logging.warning(
        "No %s products found but %s cached; waiting %ss then retrying once.",
        category["label"], len(cached), EMPTY_RESULT_RETRY_SECONDS,
    )
    deadline = time.monotonic() + EMPTY_RESULT_RETRY_SECONDS
    while not should_stop() and time.monotonic() < deadline:
        time.sleep(1)
    if should_stop():
        return [], True

    logging.info("Retrying %s after empty-result backoff...", category["label"])
    products = scrape_catalog(page, category, max_pages, should_stop)
    if products:
        logging.info("%s retry succeeded: %s products", category["label"], len(products))
        return products, False

    logging.warning("%s retry also returned 0; using cached state.", category["label"])
    return cached, True


def open_fresh_context(playwright: Any, browser_name: str, headless: bool) -> Any:
    """Launch a brand-new throwaway browser context with no saved session.
    A fresh context means no cookies or localStorage from previous scans,
    so Naver cannot track us across scan cycles.
    """
    browser = getattr(playwright, browser_name).launch(
        headless=headless,
        args=["--disable-blink-features=AutomationControlled"],
    )
    context = browser.new_context(
        locale="ko-KR",
        viewport={"width": 1280, "height": 900},
        user_agent=BROWSER_USER_AGENT,
    )
    context.add_init_script(STEALTH_SCRIPT)
    return context


def run() -> None:
    from playwright.sync_api import sync_playwright

    poll_seconds = max(60, int(os.getenv("XOPLAY_POLL_SECONDS", "480")))
    max_pages = max(1, int(os.getenv("XOPLAY_MAX_PAGES", "20")))
    run_once = os.getenv("XOPLAY_RUN_ONCE", "false").casefold() == "true"
    headless = os.getenv("XOPLAY_HEADLESS", "false").casefold() == "true"
    browser_name = os.getenv("XOPLAY_BROWSER", "chromium").casefold()
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
        while not stopping:
            # Open a fresh browser for every scan cycle so Naver sees a new
            # session each time and cannot use cookie/session history to
            # trigger a CAPTCHA on repeat visits.
            context = open_fresh_context(playwright, browser_name, headless)
            page = context.new_page()
            previous = {product["key"]: product for product in previous_list}
            combined = []
            try:
                for index, category in enumerate(NAVER_CATEGORIES):
                    if index > 0 and not stopping:
                        logging.info("Cooling down %ss before next category...", CATEGORY_COOLDOWN_SECONDS)
                        time.sleep(CATEGORY_COOLDOWN_SECONDS)
                    try:
                        products, used_cache = scrape_with_retry(
                            page, category, max_pages, previous_list, lambda: stopping
                        )
                        combined.extend(products)
                        if used_cache:
                            logging.warning(
                                "No %s products found after retry; previous state retained",
                                category["label"],
                            )
                    except Exception:
                        logging.exception("%s check failed; previous state retained", category["label"])
                        combined.extend(
                            p for p in previous_list if p.get("source") == category["source"]
                        )
            finally:
                # Always close the browser after each scan cycle
                try:
                    context.browser.close()
                except Exception:
                    pass
                logging.info("Browser closed after scan cycle.")

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


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run()
