# EU card shop monitor

A dependency-free Python monitor and GitHub Pages dashboard for the remaining live stores:

- Korean Pokémon and One Piece categories at Spielwarenparadies24; and
- Pokémon and One Piece catalogues at CrazyCardsEU.

The initial run establishes a silent baseline. Later new products and genuine restocks are sent as Discord embeds when a webhook is configured.

## Hosted architecture

GitHub Actions runs the monitor every five minutes. Each live EU shop source is checked independently, so a temporary failure in one store does not block scans for the others. These runs update the SQLite state and `docs/status.json`; GitHub Pages serves the dashboard from `docs/`. Scheduled Actions can still be delayed by GitHub during busy periods.

Discord is optional. Without it, the dashboard and Action still update. To receive Discord alerts, add a repository Actions secret named `DISCORD_WEBHOOK_URL`.

The monitor sends Discord alerts for:

- new products after the first baseline run;
- products that move from sold out to available; and
- store scan failures and recoveries, with failure alerts suppressed after the first alert until that store recovers.

## Retired sources

Pokémon Store Korea, Naver Brand Store, Xoplay, and the local browser monitor are retired from the active dashboard and live monitor path. The old local Naver monitor notes are in `Jasa-S/Archive` under `pokemon-product-monitor/local-naver-monitor/`.

## GitHub configuration

Optional repository variables:

- `WATCH_KEYWORDS`: comma-separated keywords that restrict new-product alerts.

Run **Actions → Monitor stores → Run workflow** once after adding secrets or variables.

## Dashboard

The dashboard can filter by store, availability, EUR price, and product name. Only the four live EU source IDs are exported and shown:

- `crazycards-onepiece`
- `crazycards-pokemon`
- `spielwaren-onepiece-kor`
- `spielwaren-pokemon-kor`

## Local use

```sh
DATABASE_PATH="$PWD/state/monitor.db" RUN_ONCE=true python3 monitor.py
python3 export_dashboard.py
```

Use conservative polling, do not bypass login, CAPTCHA, queues, or rate limits, and stop if a store objects. This project only observes products; it never attempts purchases.
