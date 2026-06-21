# Pokémon product monitor

A dependency-free Python monitor and GitHub Pages dashboard for:

- Pokémon Store Korea card-category products, including sold-out products and explicit restocks;
- the newest products and their stock changes in the requested Pokémon Naver Brand Store card category; and
- Xoplay discoveries through Naver's official Shopping Search API when indexed, plus an optional user-controlled local browser monitor for complete catalogue and stock checks.

The initial run establishes a silent baseline. Later new products and genuine restocks are sent as Discord embeds when a webhook is configured.

## Hosted architecture

GitHub Actions checks Pokémon Store's card category, selected restocks, and the official Naver Search API for Xoplay every five minutes. A separate daily job refreshes the complete Pokémon Store card category—including sold-out products—and attempts Naver's authoritative card-category page. Both update the SQLite state and `docs/status.json`; GitHub Pages serves the dashboard from `docs/`. Scheduled Actions can be delayed during busy periods, so five minutes is the target cadence rather than a real-time guarantee.

Discord is optional. Without it, the dashboard and Action still update. To receive Discord alerts, add a repository Actions secret named `DISCORD_WEBHOOK_URL`.

## Store support and API limitations

The Pokémon Store and Pokémon Naver Brand Store expose public, read-only product state, including availability. Tracking is restricted to Pokémon Store category `488339` and Naver Brand category `c94139abcef14362997090c5da975e28`. Pokémon Store status distinguishes fully available, partially available, and fully sold-out products by inspecting their variants. The Shopby category request explicitly includes sold-out products; only products hidden or deleted by the store remain undiscoverable. Naver restricts anonymous automation and may rate-limit GitHub-hosted requests, so the daily job attempts the newest 40 products from the exact card category and accumulates what it sees. The general Naver Shopping Search fallback is intentionally not used for the Pokémon Brand Store because it cannot prove category membership. Naver's Commerce API cannot access an unrelated seller's store without seller authorization.

To enable Xoplay Search API discovery, create a Naver Developers application with the Search API and add these repository Actions secrets:

- `NAVER_CLIENT_ID`
- `NAVER_CLIENT_SECRET`

The hosted Xoplay fallback uses six queries. At the five-minute schedule this uses at most about 1,734 of the 25,000 daily Search API requests.

### User-controlled Xoplay monitoring

Naver presents CAPTCHAs to GitHub-hosted browsers and its Search API does not reliably index Xoplay. For release windows, run the optional local monitor on a Mac or other desktop. It never starts automatically and stops completely on command.

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

The visible browser uses `.xoplay-browser/` to retain its session. Complete Naver's CAPTCHA in that window if prompted. The first successful scan silently establishes a baseline; later new products and sold-out-to-available changes trigger the existing translated Discord workflow. Changed Xoplay catalogue data is published separately to `docs/xoplay.json`, so the hosted Pokémon monitor cannot overwrite it. The local machine must remain awake and online while monitoring is enabled. Use `./xoplay-monitor once` for a single interactive check.

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

The dashboard can filter by store, fully available/partially available/sold-out status, KRW price range, and product name, and can sort by name, price, or availability. EUR prices are approximate conversions based on the latest daily ECB reference rate supplied through Frankfurter; they do not include card, bank, or merchant conversion fees.

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
