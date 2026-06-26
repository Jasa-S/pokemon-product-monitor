# Bug & Debug Session Log

> **Last updated:** 2026-06-26  
> **Purpose:** Complete human-readable summary of all known bugs, root causes, fix status, and remaining open questions — written so that an AI agent or developer picking this up cold can understand the full picture without reading the entire git history.

---

## Repo Overview

Two independent monitors live in this repo:

| Monitor | File | Stores covered | Mechanism |
|---|---|---|---|
| **Naver local monitor** | `xoplay_local_monitor.py` | `naver-pokemon` (brand.naver.com, ~170 products, 3 pages) · `naver-xoplay` (smartstore, ~45 products, 1 page) | Windows-only · Playwright persistent Chromium · controlled by `xoplay-monitor-windows.ps1` |
| **Pokémon Store monitor** | `monitor.py` | `pokemonstore` (www.pokemonstore.co.kr via Shopby API) · `naver-pokemon` brand store (NaverBrandCategoryClient) · Naver Shopping Search API | Docker (Linux) or direct Python · SQLite state at `/data/monitor.db` |

Both monitors send Discord alerts via webhook when a new product appears or a sold-out product comes back in stock.

---

## Previously Fixed Bugs (already on `main` before this session)

### 1. Empty result retry — `xoplay_local_monitor.py`
**Symptom:** When Naver's anti-scrape silently returned 0 products for Xoplay, the monitor treated it as "everything sold out" and updated state accordingly, causing a flood of false restock alerts on the next real scrape.  
**Fix:** When Xoplay returns 0 products, wait 60 seconds (`EMPTY_RESULT_RETRY_SECONDS = 60`) and retry once. If the retry also returns 0, fall back to cached state without updating. **Status: Fixed, live on main.**

### 2. Baseline sync / false positive alerts — `xoplay_local_monitor.py`
**Symptom:** On startup, the local state file (`.naver-local-monitor-state.json`) was sometimes behind the shared GitHub dashboard (`docs/local-naver.json`). When the remote state was adopted, all "new" products in it fired Discord alerts.  
**Fix:** When the local state is synced from the remote, a `synced_from_remote` flag is set and **all** alert events are suppressed for that cycle.  
**Status: Fixed, but see Bug #4 below — this fix is too broad and causes a new miss.**

### 3. Wrong prices — `xoplay_local_monitor.py`
**Symptom:** All products showed ~3,000 KRW because `min()` was used to extract a price from the card text, and it kept selecting the loyalty points reward value (e.g. "3,000원 포인트") instead of the real sale price.  
**Fix:** Replaced `min()` with `extract_sale_price()` — returns the **first price ≥ 1,000 KRW** found in the card text, skipping loyalty point values.  
**Status: Fixed, live on main.**

---

## Bugs Investigated in This Session (2026-06-26)

**Context:** On 2026-06-26 at approximately 10:00 AM KST, new product releases went live on both Naver stores and pokemonstore.co.kr. Two problems were observed:
1. Xoplay (naver-xoplay): product was visible in logs as found/scraped, but no Discord notification was sent.
2. pokemonstore.co.kr: Discord notification arrived many hours late. The product was already on the website well before the alert fired.

---

### Bug #4 — Xoplay: product found but no Discord alert sent
**File:** `xoplay_local_monitor.py`  
**Status: IDENTIFIED, NOT YET FIXED in code**

**Root cause:**  
The `synced_from_remote` flag introduced in fix #2 suppresses *all* alerts for the entire cycle whenever the remote dashboard state is newer than the local state. This can happen on any regular cycle, not just on first boot — for example, whenever another machine or process has pushed a newer `docs/local-naver.json` to GitHub.

The relevant code pattern:
```python
events = [] if synced_from_remote else scan_events(previous, combined, ...)
```

When the release dropped at 10 AM KST, the monitor happened to run a cycle where the remote state was marginally newer → `synced_from_remote = True` → all events suppressed → no Discord alert, even though the new Xoplay product was correctly scraped and detected.

**Correct fix (not yet implemented):**  
Instead of blanket event suppression, only suppress events for products whose keys already existed in the adopted remote baseline. Genuinely new products (keys absent from the remote state) should still fire alerts:
```python
events = scan_events(previous, combined, previous_categories, current_categories)
if synced_from_remote:
    remote_keys = {p["key"] for p in previous_list}
    events = [(evt, p) for evt, p in events if p["key"] not in remote_keys]
```

---

### Bug #5 — pokemonstore: Discord notification hours late
**File:** `monitor.py`  
**Status: FIXED — commit `bf3238f` on main**

**Root cause:**  
`monitor.py` uses a SQLite DB (`/data/monitor.db`) with a table `initialized_feeds` to track whether a feed has been seen before. On first observation of a feed, if `feed_initialized(feed)` returns `False`, new products are **silently primed** (stored in DB, no Discord alert) to avoid flooding on first run.

When the Docker container or Python process is restarted, the DB file is re-opened. However, `initialized_feeds` rows are *inside* the same DB file on a persistent volume — but the `feed_initialized()` check still returned `False` after restarts in some conditions (e.g. if the volume was re-created, or if the DB was initialized fresh).

**Exact flow that caused the miss:**
1. Monitor was restarted (or the container was briefly stopped) around 10 AM KST.
2. On the first `check_once()` after restart, `feed_initialized("pokemonstore-card-arrivals")` → `False`.
3. `new_arrivals()` scraped and found the new release correctly.
4. In `observe_products()`: `previous is None` (not in DB yet) AND `initialized is False` → fell into the `logging.info("Primed %s")` branch, no Discord call.
5. The product was stored in DB. Every subsequent cycle: `previous is not None` → no "new product" event ever fired.
6. The alert that did eventually arrive came hours later via the **Naver Shopping Search API** path (`naver-pokemon-tcg-search-v7` feed), once Naver's own search index caught up with the new listing.

**Fix applied (commit `bf3238f`):**  
Added `State.ensure_feeds_initialized_from_existing_products()`, called once at the top of `check_once()`. It checks whether any product with `source:pokemonstore` or `source:naver-pokemon` already exists in the DB. If yes, it auto-marks all related feeds as initialized — so a restarted process that inherits a populated DB behaves as if it was never restarted. On a genuinely empty DB (true first run), nothing is marked, preserving the silent-prime behaviour.

```python
# In check_once(), top of function:
state.ensure_feeds_initialized_from_existing_products({
    "pokemonstore": [
        "pokemonstore-card-arrivals",
        "pokemonstore-card-catalog",
        "pokemonstore-watchlist",
    ],
    "naver-pokemon": [
        "naver-pokemon-card-category",
        "naver-pokemon-tcg-search-v7",
    ],
})
```

---

## Open Issues / Not Yet Fixed

| # | File | Issue | Severity |
|---|---|---|---|
| 4 | `xoplay_local_monitor.py` | `synced_from_remote` suppresses alerts for genuinely new products when remote state is marginally newer | High — can silently swallow release alerts |
| — | `xoplay_local_monitor.py` | Sequential scraping: Xoplay is scraped first; if its 60s empty-result retry fires, the naver-pokemon scrape is delayed by ~2+ minutes | Medium — adds latency to pokemon alerts on same cycle |

---

## Architecture Notes for AI Agents

- **Two separate state systems:** `xoplay_local_monitor.py` uses a JSON file (`.naver-local-monitor-state.json`) synced to/from `docs/local-naver.json` on GitHub. `monitor.py` uses a SQLite DB at `/data/monitor.db`. They do not share state.
- **Feed initialization pattern in `monitor.py`:** The `initialized_feeds` SQLite table is the gating mechanism for "first run silence". Any bug that causes this table to appear empty on startup will cause new products to be primed silently. The fix in `bf3238f` mitigates this via DB product existence checks, but if the DB itself is deleted/reset, the first cycle after a real release will still prime silently.
- **`synced_from_remote` flag in `xoplay_local_monitor.py`:** Set in `run()` when remote JSON timestamp > local timestamp. Currently suppresses all events. Should be changed to suppress only events for products already present in the remote baseline (see Bug #4 fix suggestion above).
- **Naver anti-scrape:** Xoplay smartstore silently returns 0 products when blocked. The 60s retry handles one consecutive block. Multiple consecutive blocks will fall back to cached state without alerting (correct behaviour — avoids false "sold out" events).
- **`NOTIFY_ON_FIRST_RUN` env var:** If set to `true`, `monitor.py` will fire Discord alerts on the very first cycle even on a fresh DB. Useful for testing. Default is `false`.
