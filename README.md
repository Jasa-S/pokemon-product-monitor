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
3. Click **Watch restock** on each desired product.
4. Click **Copy numbers**.
5. Open the dashboard's **Open GitHub variables** link, create or edit `WATCH_PRODUCT_NOS`, and paste the comma-separated value.
6. Run the **Monitor stores** workflow once. Afterwards, it checks those product detail endpoints every ten minutes.

The daily catalog refresh keeps all products visible without hammering the store. Only selected product numbers get frequent, authoritative restock alerts.

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
