import os
from dataclasses import dataclass

@dataclass(frozen=True)
class Settings:
    reference_rates_url: str
    xe_url: str
    dab_url: str
    gold_url: str
    gold_api_key: str | None
    base_currency: str

def load_settings() -> Settings:
    return Settings(
        reference_rates_url=os.getenv(
            "REFERENCE_RATES_URL",
            "https://open.er-api.com/v6/latest/USD",
        ),
        xe_url=os.getenv("XE_RATES_URL", ""),
        dab_url=os.getenv("DAB_RATES_URL", ""),
        gold_url=os.getenv("GOLD_API_URL", ""),
        gold_api_key=os.getenv("GOLD_API_KEY"),
        base_currency=os.getenv("BASE_CURRENCY", "USD"),
    )
