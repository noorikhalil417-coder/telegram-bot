# SARWARI EXCHANGE Telegram Bot

## Structure

- `bot.py` — main Telegram bot (based on the uploaded source)
- `config.py` — environment/configuration
- `models.py` — provider result model
- `services/rates.py` — currency/gold provider layer
- `utils/formatting.py` — Telegram message formatting
- `webapp/index.html` — Telegram Web App
- `requirements.txt` — Python dependencies
- `Dockerfile` — Fly.io runtime image
- `fly.toml` — Fly.io app configuration
- `.github/workflows/deploy-webapp-pages.yml` — GitHub Pages deployment

## Required Fly.io secrets

Set these in Fly.io:

- `BOT_TOKEN` — Telegram bot token
- `ADMIN_CHAT_ID` — Telegram ID of the administrator
- `WEB_APP_URL` — deployed GitHub Pages URL

Optional provider settings:

- `REFERENCE_RATES_URL`
- `XE_RATES_URL`
- `DAB_RATES_URL`
- `GOLD_API_URL`
- `GOLD_API_KEY`

Do not commit tokens or API keys to GitHub.

## Important

The bot currently stores remittance requests in RAM dictionaries. A Fly.io restart will clear them. For production financial/remittance use, move remittance/customer data to persistent storage such as PostgreSQL before relying on it for real transactions.
