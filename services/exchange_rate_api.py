import logging
import math
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import requests

from models import ProviderResult, Rate

EXCHANGE_RATE_API_URL = "https://open.er-api.com/v6/latest/USD"
REFERENCE_CURRENCIES = ("AFN", "EUR", "GBP", "PKR", "INR", "IRR")


class ExchangeRateAPIProvider:
    source = "ExchangeRate-API (reference)"

    def __init__(self, timeout: float = 15.0, retries: int = 2):
        self.timeout = timeout
        self.retries = retries

    def fetch(self) -> ProviderResult:
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                response = requests.get(EXCHANGE_RATE_API_URL, timeout=self.timeout)
                response.raise_for_status()
                return self._parse(response.json(), retrieved_at=datetime.now(timezone.utc))
            except (requests.RequestException, ValueError, TypeError, KeyError) as error:
                last_error = error
                if attempt < self.retries:
                    continue
        logging.warning("ExchangeRate-API failed: %s", last_error)
        return ProviderResult(available=False, message="ExchangeRate-API فعلاً در دسترس نیست.")

    def _parse(self, data: Any, retrieved_at: datetime | None = None) -> ProviderResult:
        if not isinstance(data, dict) or data.get("result") != "success":
            raise ValueError("ExchangeRate-API returned an invalid response")
        rates_data = data.get("rates")
        if not isinstance(rates_data, dict):
            raise ValueError("ExchangeRate-API response has no rates")
        timestamp = self._parse_timestamp(data.get("time_last_update_utc"))
        next_update = self._parse_timestamp(data.get("time_next_update_utc"), required=False)
        retrieved_at = retrieved_at or datetime.now(timezone.utc)
        rates: list[Rate] = []
        for quote in REFERENCE_CURRENCIES:
            value = rates_data.get(quote)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(f"ExchangeRate-API missing valid {quote} rate")
            rates.append(Rate(
                symbol=f"USD-{quote}",
                base_currency="USD",
                quote_currency=quote,
                value=float(value),
                source=self.source,
                rate_type="mid_market",
                timestamp=timestamp,
                next_update=next_update,
                rate_direction="quote_per_base",
                source_unit=quote,
                retrieved_at=retrieved_at,
                source_updated_at=timestamp,
            ))
        return ProviderResult(rates=tuple(rates))

    @staticmethod
    def _parse_timestamp(value: Any, required: bool = True) -> datetime | None:
        if not value:
            if required:
                raise ValueError("ExchangeRate-API response has no update timestamp")
            return None
        raw_value = str(value)
        try:
            timestamp = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
        except ValueError:
            timestamp = parsedate_to_datetime(raw_value)
        return timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)