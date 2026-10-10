import logging
from datetime import datetime, timezone
from typing import Any

import requests

from models import ProviderResult, Rate

XE_URL = "https://xecdapi.xe.com/v1/convert_from.json"


class XEProvider:
    source = "XE (mid-market)"

    def __init__(self, api_key: str | None, account_id: str | None, timeout: float = 15.0):
        self.api_key = api_key
        self.account_id = account_id
        self.timeout = timeout

    def fetch(self) -> ProviderResult:
        if not self.api_key or not self.account_id:
            return ProviderResult(
                available=False,
                message="XE برای استفاده به XE_API_KEY و XE_ACCOUNT_ID نیاز دارد.",
            )
        try:
            response = requests.get(
                XE_URL,
                params={"from": "USD", "to": "AFN,EUR,PKR"},
                auth=(self.account_id, self.api_key),
                timeout=self.timeout,
            )
            response.raise_for_status()
            data: Any = response.json()
            retrieved_at = datetime.now(timezone.utc)
            rates: list[Rate] = []
            for item in data.get("to", []):
                quote = str(item["quotecurrency"])
                value = float(item["mid"])
                if value <= 0:
                    raise ValueError("XE returned an invalid rate")
                rates.append(Rate(
                    "USD" + quote,
                    "USD",
                    quote,
                    value,
                    self.source,
                    "mid_market",
                    retrieved_at,
                    rate_direction="quote_per_base",
                    source_unit=quote,
                    retrieved_at=retrieved_at,
                    source_updated_at=None,
                ))
            if not rates:
                raise ValueError("XE returned no rates")
            return ProviderResult(rates=tuple(rates))
        except (requests.RequestException, ValueError, TypeError, KeyError) as error:
            logging.warning("XE API failed: %s", error)
            return ProviderResult(available=False, message="XE فعلاً در دسترس نیست.")
