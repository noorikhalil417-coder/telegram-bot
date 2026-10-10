import os
from dataclasses import dataclass

from remittance_store import DEFAULT_DATABASE_PATH

EUR_DEDUCTION = 0.50
OTHER_CURRENCY_DEDUCTION = 50.0


@dataclass(frozen=True)
class Settings:
    bot_token: str
    xe_api_key: str | None = None
    xe_account_id: str | None = None
    request_timeout: float = 15.0
    cache_ttl_seconds: int = 60
    reference_cache_ttl_seconds: int = 86400
    eur_deduction: float = EUR_DEDUCTION
    other_currency_deduction: float = OTHER_CURRENCY_DEDUCTION
    database_path: str = DEFAULT_DATABASE_PATH


def load_settings() -> Settings:
    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        raise RuntimeError("BOT_TOKEN environment variable is not set")

    return Settings(
        bot_token=bot_token,
        xe_api_key=os.getenv("XE_API_KEY"),
        xe_account_id=os.getenv("XE_ACCOUNT_ID"),
        request_timeout=float(os.getenv("REQUEST_TIMEOUT", "15")),
        cache_ttl_seconds=int(os.getenv("CACHE_TTL_SECONDS", "60")),
        reference_cache_ttl_seconds=int(os.getenv("REFERENCE_CACHE_TTL_SECONDS", "86400")),
        eur_deduction=float(os.getenv("EUR_DEDUCTION", str(EUR_DEDUCTION))),
        other_currency_deduction=float(
            os.getenv("OTHER_CURRENCY_DEDUCTION", str(OTHER_CURRENCY_DEDUCTION))
        ),
        database_path=os.getenv("DATABASE_PATH", DEFAULT_DATABASE_PATH),
    )
