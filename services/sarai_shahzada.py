import logging
import math
import re
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from config import Settings
from models import ProviderResult, Rate


SARAI_SHAHZADA_URL = "https://sarafi.af/fa/exchange-rates/sarai-shahzada"
SOURCE = "Sarai Shahzada (sarafi.af)"
DISPLAY_ORDER = (
    "USD", "EUR", "IRR", "PKR", "JPY", "GBP", "SAR", "AED", "CHF", "AUD",
    "CAD", "RUB", "DKK", "SEK", "NOK", "TRY", "CNY", "KWD", "QAR", "BHD",
)


class SaraiShahzadaProvider:
    source = SOURCE

    def __init__(self, settings: Settings):
        self.timeout = settings.request_timeout

    def fetch(self) -> ProviderResult:
        try:
            response = requests.get(SARAI_SHAHZADA_URL, timeout=self.timeout)
            response.raise_for_status()
            retrieved_at = datetime.now(timezone.utc)
            rates = self._parse(response.text, retrieved_at=retrieved_at)
            if not rates:
                raise ValueError("Sarai Shahzada returned no rates")
            return ProviderResult(rates=tuple(rates))
        except (requests.RequestException, ValueError, TypeError, KeyError) as error:
            logging.warning("Sarai Shahzada failed: %s", error)
            return ProviderResult(available=False, message="منبع نرخ ارز فعلاً در دسترس نیست.")

    def _parse(self, html: str, retrieved_at: datetime | None = None) -> list[Rate]:
        soup = BeautifulSoup(html, "html.parser")
        table = self._find_rates_table(soup)
        retrieved_at = retrieved_at or datetime.now(timezone.utc)
        parsed: dict[str, tuple[str, float, float]] = {}

        for row in table.find_all("tr"):
            cells = [" ".join(cell.stripped_strings) for cell in row.find_all(["th", "td"])]
            if len(cells) < 3 or not re.match(r"^[A-Z]{3}\s*-", cells[0]):
                continue
            symbol = cells[0][:3]
            buy = self._number(cells[1])
            sell = self._number(cells[2])
            parsed[symbol] = (cells[0], buy, sell)

        rates: list[Rate] = []
        for symbol in DISPLAY_ORDER:
            if symbol not in parsed:
                continue
            label, buy, sell = parsed[symbol]
            for rate_type, value in (("cash_buy", buy), ("cash_sell", sell)):
                rates.append(Rate(
                    symbol=f"AFN-{symbol}-{rate_type}",
                    base_currency="AFN",
                    quote_currency=symbol,
                    value=value,
                    source=f"{self.source} ({label})",
                    rate_type=rate_type,
                    timestamp=retrieved_at,
                    rate_direction="source_unspecified",
                    source_unit=label,
                    retrieved_at=retrieved_at,
                    source_updated_at=None,
                ))
        return rates

    @staticmethod
    def _find_rates_table(soup: BeautifulSoup):
        for table in soup.find_all("table"):
            headers = [" ".join(cell.stripped_strings) for cell in table.find_all("th")]
            if len(headers) >= 3 and headers[0] == "واحد پول" and headers[1:3] == ["خرید", "فروش"]:
                return table
        raise ValueError("Sarai Shahzada rate table was not found")

    @staticmethod
    def _number(value: str) -> float:
        number = float(value.replace(",", "").strip())
        if not math.isfinite(number) or number <= 0:
            raise ValueError("Sarai Shahzada returned an invalid rate")
        return number
