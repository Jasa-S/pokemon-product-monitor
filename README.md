# Pokémon product monitor

A dependency-free Python monitor and GitHub Pages dashboard for:

- Pokémon Store Korea card-category products, including sold-out products and explicit restocks;
- Korean Pokémon and One Piece categories at Spielwarenparadies24; and
- the Pokémon and One Piece catalogues at CrazyCardsEU.

The initial run establishes a silent baseline. Later new products and genuine restocks are sent as Discord embeds when a webhook is configured.

## Hosted architecture

GitHub Actions checks Pokémon Store every five minutes. The four German/EU category pages are checked at most every ten minutes to avoid unnecessary load. A separate daily job refreshes the complete Pokémon Store card category—including sold-out products. These jobs update the SQLite state and `docs/status.json`; GitHub Pages serves the dashboard from `docs/`. Scheduled Actions can be delayed during busy periods.

Discord is optional. Without it, the dashboard and Action still update. To receive Discord alerts, add a repository Actions secret named `DISCORD_WEBHOOK_URL`.

## Store support and API limitations

The Pokémon Store exposes public, read-only product state, including availability. Tracking is restricted to Pokémon Store category `488339`. Pokémon Store status distinguishes fully available, partially available, and fully sold-out products by inspecting their variants. The Shopby category request explicitly includes sold-out products; only products hidden or deleted by the store remain undiscoverable.

Naver Brand Store, Xoplay, and the local browser monitor have been retired from the active dashboard. The old local Naver monitor files and notes were moved to `Jasa-S/Archive` under `pokemon-product-monitor/local-naver-monitor/`.

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
