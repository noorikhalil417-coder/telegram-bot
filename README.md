# SARWARI EXCHANGE Telegram Bot

## Railway deployment

Railway should use the repository root `Dockerfile` and start the bot with
`python bot.py`.

Set these Railway service variables:

- `BOT_TOKEN` — Telegram bot token. Store it as a Railway secret; never commit it.
- `ADMIN_CHAT_ID` — authorized administrator's Telegram chat ID.
- `DATABASE_PATH` — path on a mounted persistent volume, for example
  `/data/sarwari_exchange.sqlite3`.
- `WEB_APP_URL` — optional public URL for the Telegram Web App.

Mount a Railway Volume at `/data` (or change `DATABASE_PATH` to the volume's
mount path). Without a persistent volume, SQLite data may be lost when Railway
replaces the container.

Optional XE provider credentials are `XE_API_KEY` and `XE_ACCOUNT_ID`. They are
not needed for the default Frankfurter reference-rate provider. Do not put API
keys in source files or Docker build arguments.

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
