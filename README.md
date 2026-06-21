# Pokémon product monitor

A dependency-free Python monitor and GitHub Pages dashboard for:

- the complete Pokémon Store Korea catalog, **NEW ARRIVAL** products, and explicit product restocks;
- the 40 newest products and their stock changes in the requested Pokémon Naver Brand Store card category; and
- Xoplay discoveries through Naver's official Shopping Search API when credentials are configured.

The initial run establishes a silent baseline. Later new products and genuine restocks are sent as Discord embeds when a webhook is configured.

## Hosted architecture

GitHub Actions checks new arrivals and selected restocks every ten minutes. A separate daily job refreshes the complete Pokémon Store catalog in 100-item pages. Both update the SQLite state and `docs/status.json`; GitHub Pages serves the dashboard from `docs/`. Scheduled Actions can be delayed during busy periods, so this is a convenient monitor rather than a real-time guarantee.

Discord is optional. Without it, the dashboard and Action still update. To receive Discord alerts, add a repository Actions secret named `DISCORD_WEBHOOK_URL`.

## Store support and API limitations

The Pokémon Store and Pokémon Naver Brand Store expose public, read-only product state, including availability. The Brand Store server-renders the newest 40 items in this category; older products are outside this monitor's reliable public view. Naver's Commerce API cannot access an unrelated seller's store: it requires seller authorization. Xoplay also redirects anonymous category requests to Naver login, so its integration uses the official Naver Shopping Search API as a best-effort **new-product discovery** source. Search results do not provide authoritative inventory; Xoplay restock alerts are therefore deliberately disabled.

To enable Xoplay discovery, create a Naver Developers application with the Search API and add these repository Actions secrets:

- `NAVER_CLIENT_ID`
- `NAVER_CLIENT_SECRET`

The defaults search `포켓몬카드` and `포켓몬 카드`, then retain results whose link belongs to `smartstore.naver.com/xoplay`.

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

The daily catalog refresh keeps all products visible without hammering the store. Only selected product numbers get frequent, authoritative restock alerts.

Discord alerts include the original Korean title, an English title generated through GitHub Models when available, product number, price, store, image, and direct product link. Run **Actions → Test Discord notification → Run workflow** after configuring the webhook to verify delivery.

The dashboard can filter by store, in-stock/unavailable status, KRW price range, and product name, and can sort by name, price, or availability. EUR prices are approximate conversions based on the latest daily ECB reference rate supplied through Frankfurter; they do not include card, bank, or merchant conversion fees.

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
