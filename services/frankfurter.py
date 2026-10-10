import logging
import math
from datetime import date, datetime, time, timezone
from typing import Any

import requests

from models import ProviderResult, Rate

FRANKFURTER_RATES_URL = "https://api.frankfurter.dev/v2/rates"
FRANKFURTER_DAB_RATES_URL = "https://api.frankfurter.dev/v2/providers/dab/rates"
REFERENCE_CURRENCIES = (
    "AFN", "EUR", "GBP", "CHF", "INR", "PKR", "IRR", "CNY", "AED", "SAR",
)


class FrankfurterProvider:
    source = "Frankfurter (daily reference / mid-market)"
    dab_source = "Da Afghanistan Bank via Frankfurter (indicative)"

    def __init__(self, timeout: float = 12.0, retries: int = 2):
        self.timeout = timeout
        self.retries = retries

    def fetch(self) -> ProviderResult:
        return self._fetch(
            FRANKFURTER_RATES_URL,
            params={"base": "USD", "quotes": ",".join(REFERENCE_CURRENCIES)},
            source=self.source,
            rate_type="mid_market",
            expected_quotes=set(REFERENCE_CURRENCIES),
        )

    def fetch_dab_indicative_afn(self) -> ProviderResult:
        return self._fetch(
            FRANKFURTER_DAB_RATES_URL,
            params={"base": "USD", "quotes": "AFN"},
            source=self.dab_source,
            rate_type="indicative",
            expected_quotes={"AFN"},
        )

    def _fetch(
        self,
        url: str,
        *,
        params: dict[str, str],
        source: str,
        rate_type: str,
        expected_quotes: set[str],
    ) -> ProviderResult:
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                response = requests.get(url, params=params, timeout=self.timeout)
                response.raise_for_status()
                return self._parse(
                    response.json(),
                    source=source,
                    rate_type=rate_type,
                    expected_quotes=expected_quotes,
                    retrieved_at=datetime.now(timezone.utc),
                )
            except (requests.RequestException, ValueError, TypeError, KeyError) as error:
                last_error = error
                if attempt < self.retries:
                    continue
        logging.warning("Frankfurter request failed: %s", last_error)
        return ProviderResult(
            available=False,
            message="نرخ مرجع Frankfurter فعلاً در دسترس نیست.",
        )

    @staticmethod
    def _parse(
        data: Any,
        *,
        source: str,
        rate_type: str,
        expected_quotes: set[str],
        retrieved_at: datetime | None = None,
    ) -> ProviderResult:
        if not isinstance(data, list):
            raise ValueError("Frankfurter response must be a list")
        retrieved_at = retrieved_at or datetime.now(timezone.utc)
        rates: list[Rate] = []
        for item in data:
            if not isinstance(item, dict):
                raise ValueError("Frankfurter returned an invalid rate row")
            base = str(item["base"]).upper()
            quote = str(item["quote"]).upper()
            raw_value = item["rate"]
            if (
                base != "USD"
                or quote not in expected_quotes
                or isinstance(raw_value, bool)
                or not isinstance(raw_value, (int, float))
                or not math.isfinite(raw_value)
                or raw_value <= 0
            ):
                raise ValueError("Frankfurter returned an invalid currency rate")
            source_date = date.fromisoformat(str(item["date"]))
            source_updated_at = datetime.combine(
                source_date, time.min, tzinfo=timezone.utc
            )
            source_unit = (
                "IRR (rial; not converted to toman)"
                if quote == "IRR"
                else quote
            )
            rates.append(Rate(
                symbol=f"{base}-{quote}",
                base_currency=base,
                quote_currency=quote,
                value=float(raw_value),
                source=source,
                rate_type=rate_type,
                timestamp=source_updated_at,
                rate_direction="quote_per_base",
                source_unit=source_unit,
                retrieved_at=retrieved_at,
                source_updated_at=source_updated_at,
            ))
        if not rates:
            raise ValueError("Frankfurter returned no requested currency rates")
        if rate_type == "mid_market" and {rate.quote_currency for rate in rates} != expected_quotes:
            raise ValueError("Frankfurter returned an incomplete reference-rate set")
        return ProviderResult(rates=tuple(rates))
