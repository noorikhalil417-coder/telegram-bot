# SARWARI EXCHANGE Telegram Bot

## Railway deployment

Railway should use the repository root `Dockerfile` and start the bot with
`python bot.py`.

Set these Railway service variables:

- `BOT_TOKEN` — Telegram bot token. Store it as a Railway secret; never commit it.
- `ADMIN_CHAT_ID` — authorized administrator Telegram ID; multiple IDs may be
  separated with commas or semicolons.
- `DATABASE_PATH` — path on a mounted persistent volume, for example
  `/data/sarwari_exchange.sqlite3`.
- `WEB_APP_URL` — optional public URL for the Telegram Web App.
- `DAILY_REPORT_CHAT_ID` — optional channel/group ID for daily rate reports. The
  bot must be allowed to post there; reports can then be enabled and scheduled
  in `/adminrates`.

Mount a Railway Volume at `/data` and set `DATABASE_PATH` to
`/data/sarwari_exchange.sqlite3` (or use the volume's actual mount path).
Without a persistent volume, SQLite data may be lost when Railway replaces the
container. The local default path is `data/sarwari_exchange.sqlite3`.

The automatic local currency source is the public cash buy/sell table at
`https://sarafi.af/fa/exchange-rates`; background refreshes are spaced at least
15 minutes apart, while an administrator can request a forced refresh. Its page
gives a time-of-day but not a dated source update time; the bot records retrieval
time separately and does not claim that time is a live source timestamp. PKR
values are quoted per 1,000 rupees and IRR values per 10,000 rials (1,000 toman);
conversions apply those scales and show the actual currency units. Rates are
not fabricated when the provider fails; cached values are marked stale.

Automatic gold pricing is disabled because no reliable local provider with a
verified unit is configured. An authorized administrator can record a manual
local or international-spot gold price, including unit, purity and optional
`SOURCE=...` label, in `/adminrates`. Rates are previewed before saving and
manual changes are audited in SQLite.

No paid provider key is required for the implemented Sarafi.af rate source.
Optional `XE_API_KEY` and `XE_ACCOUNT_ID` are only used if an XE integration is
configured; never put API keys in source files or Docker build arguments.

The `fly.toml` file is retained for the existing Fly.io configuration; Railway
does not use it.

## Project structure

- `bot.py` — Telegram handlers and application entry point
- `config.py` — environment settings
- `models.py` — provider data models
- `remittance_store.py` — SQLite persistence for remittances and managed rates
- `services/` — rate-provider and rate-management services
- `utils/` — cache and message formatting
- `webapp/` — Telegram Web App files
- `tests/` — unit tests
- `Dockerfile` and `.dockerignore` — Railway container build

## Local checks

Run the tests with:

```sh
python -m unittest discover -s tests -v
```

Compile Python files with:

```sh
python -m compileall -q .
```
