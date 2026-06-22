# Pokémon product monitor

A dependency-free Python monitor and GitHub Pages dashboard for:

- Pokémon Store Korea card-category products, including sold-out products and explicit restocks;
- the newest products and their stock changes in the requested Pokémon Naver Brand Store card category; and
- Xoplay discoveries through Naver Search plus an optional user-controlled WebKit monitor;
- Korean Pokémon and One Piece categories at Spielwarenparadies24; and
- the Pokémon and One Piece catalogues at CrazyCardsEU.

The initial run establishes a silent baseline. Later new products and genuine restocks are sent as Discord embeds when a webhook is configured.

## Hosted architecture

GitHub Actions checks Pokémon Store every five minutes. The four German/EU category pages are checked at most every ten minutes to avoid unnecessary load. A separate daily job refreshes the complete Pokémon Store card category—including sold-out products—and attempts Naver's authoritative card-category page. These jobs update the SQLite state and `docs/status.json`; GitHub Pages serves the dashboard from `docs/`. Scheduled Actions can be delayed during busy periods.

Discord is optional. Without it, the dashboard and Action still update. To receive Discord alerts, add a repository Actions secret named `DISCORD_WEBHOOK_URL`.

## Store support and API limitations

The Pokémon Store and Pokémon Naver Brand Store expose public, read-only product state, including availability. Tracking is restricted to Pokémon Store category `488339` and Naver Brand category `c94139abcef14362997090c5da975e28`. Pokémon Store status distinguishes fully available, partially available, and fully sold-out products by inspecting their variants. The Shopby category request explicitly includes sold-out products; only products hidden or deleted by the store remain undiscoverable. Naver restricts anonymous automation and may rate-limit GitHub-hosted requests, so the daily job attempts the newest 40 products from the exact card category and accumulates what it sees. The Naver Shopping Search fallback also restores card-title discoveries whose links belong to the Pokémon Brand Store. Those results are clearly marked `Unknown`, never generate restock alerts, and may be incomplete because Search cannot prove category membership. Naver's Commerce API cannot access an unrelated seller's store without seller authorization.

To enable discovery-only Pokémon Brand and Xoplay Search API feeds, create a Naver Developers application with the Search API and add these repository Actions secrets:

- `NAVER_CLIENT_ID`
- `NAVER_CLIENT_SECRET`

Naver has two different limits. The documented non-login Search API quota is 25,000 calls per day per client ID. The hosted Pokémon and Xoplay fallbacks use eleven queries every five minutes plus the daily catalogue run, or at most about 3,179 calls per day (12.7%). The storefront's HTTP 429/CAPTCHA behavior is separate anti-automation protection with no published fixed request allowance; reducing Search API calls cannot remove that storefront block. The local WebKit monitor solves the architecture problem by using a user-controlled, logged-in residential browser session without bypassing Naver verification.

### User-controlled Naver monitoring

Naver presents CAPTCHAs to GitHub-hosted browsers and its Search API does not reliably index Xoplay or expose exact Brand Store category membership. For release windows, run the optional local monitor on a Mac. It checks both Xoplay and the exact Naver Pokémon card category. It never starts automatically and stops completely on command.

One-time setup:

```sh
./xoplay-monitor setup
```

Turn it on only when wanted:

```sh
./xoplay-monitor start
./xoplay-monitor status
./xoplay-monitor logs
./xoplay-monitor stop
```

The visible browser is Playwright WebKit—the closest supported engine to Safari, though not the branded Safari application—and uses `.naver-webkit-profile/` to retain its session. Complete Naver login or CAPTCHA yourself in that window if prompted. The first successful scan silently establishes a baseline; later new products and sold-out-to-available changes trigger translated Discord alerts. Local Naver data is published separately to `docs/local-naver.json`, so hosted jobs cannot overwrite it. The Mac must remain awake and online while monitoring is enabled. Use `./xoplay-monitor once` for a single interactive check.

## GitHub configuration

Optional repository variables:

- `WATCH_PRODUCT_NOS`: comma-separated Pokémon Store product numbers.
- `WATCH_KEYWORDS`: comma-separated keywords that restrict new-product alerts.

Run **Actions → Monitor stores → Run workflow** once after adding secrets or variables.

### Selecting restock products

1. Open the GitHub Pages dashboard.
2. Filter to `pokemonstore` and search for a product.
3. Click **Watch restock** on the desired product.
4. Submit the prefilled GitHub issue. A workflow validates the product, updates `watchlist.json`, refreshes the dashboard, and closes the request automatically.
5. The monitor checks watched product detail endpoints every ten minutes. Click **✓ Watching — remove** to create the corresponding removal request.

The daily catalog refresh keeps all card-category products visible without hammering the store. Only selected product numbers get frequent, authoritative detail-page restock alerts.

Discord alerts include the original Korean title, an English title generated through GitHub Models when available, product number, price, store, image, and direct product link. Run **Actions → Test Discord notification → Run workflow** after configuring the webhook to verify delivery.

The dashboard can filter by store, currency, availability, native price, and product name. Korean prices receive an approximate EUR conversion based on the latest daily ECB reference rate supplied through Frankfurter. German/EU shops display their native EUR prices.

## Local use

```sh
cp .env.example .env
docker compose up -d --build
```

Or:

```sh
DATABASE_PATH="$PWD/state/monitor.db" RUN_ONCE=true python3 monitor.py
python3 export_dashboard.py
```

Tests:

```sh
python3 -m unittest -v
```

Use conservative polling, do not bypass login, CAPTCHA, queues, or rate limits, and stop if a store objects. This project only observes products; it never attempts purchases.
