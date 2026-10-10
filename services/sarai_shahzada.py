import logging
import math
import re
from datetime import datetime, timezone
from time import sleep

import requests
from bs4 import BeautifulSoup

from config import Settings
from models import ProviderResult, Rate


SARAI_SHAHZADA_URL = "https://sarafi.af/fa/exchange-rates"
SOURCE = "Sarai Shahzada (sarafi.af)"
DISPLAY_ORDER = (
    "USD", "EUR", "IRR", "PKR", "JPY", "GBP", "SAR", "AED", "CHF", "AUD",
    "CAD", "RUB", "DKK", "SEK", "NOK", "TRY", "CNY", "KWD", "QAR", "BHD",
)
REQUIRED_CURRENCIES = {"USD", "EUR", "PKR", "IRR", "AED"}
BASE_UNIT_SCALES = {"PKR": 1000.0, "IRR": 10000.0}


class SaraiShahzadaProvider:
    source = SOURCE

    def __init__(self, settings: Settings):
        self.timeout = settings.request_timeout

    def fetch(self) -> ProviderResult:
        for attempt in range(2):
            try:
                response = requests.get(
                    SARAI_SHAHZADA_URL,
                    timeout=self.timeout,
                    headers={"User-Agent": "SARWARI-EXCHANGE/1.0 (rate display)"},
                )
                response.raise_for_status()
            except requests.RequestException as error:
                if attempt == 0:
                    sleep(0.25)
                    continue
                logging.warning("Sarai Shahzada request failed: %s", error)
                return ProviderResult(
                    available=False,
                    message=f"منبع نرخ ارز در دسترس نیست ({type(error).__name__}).",
                )
            try:
                retrieved_at = datetime.now(timezone.utc)
                rates = self._parse(response.text, retrieved_at=retrieved_at)
                return ProviderResult(rates=tuple(rates))
            except (ValueError, TypeError, KeyError) as error:
                logging.warning("Sarai Shahzada response was invalid: %s", error)
                return ProviderResult(
                    available=False,
                    message=f"دادهٔ منبع نرخ ناقص یا نامعتبر است ({type(error).__name__}).",
                )
        return ProviderResult(
            available=False, message="منبع نرخ ارز فعلاً در دسترس نیست."
        )

    def _parse(self, html: str, retrieved_at: datetime | None = None) -> list[Rate]:
        soup = BeautifulSoup(html, "html.parser")
        table = self._find_rates_table(soup)
        retrieved_at = retrieved_at or datetime.now(timezone.utc)
        parsed: dict[str, tuple[str, float, float]] = {}

        for row in table.find_all("tr"):
            cells = row.find_all(["th", "td"])
            if len(cells) < 3:
                continue
            label = " ".join(cells[0].stripped_strings)
            link = cells[0].find("a", href=True)
            pair = re.search(
                r"/exchange-rates/sarai-shahzada/([A-Z]{3})-AFN(?:$|[/?#])",
                link["href"] if link else "",
            )
            if not pair or not re.match(r"^[A-Z]{3}\s*-", label):
                continue
            symbol = pair.group(1)
            if symbol in parsed:
                raise ValueError(f"Sarai Shahzada duplicated the {symbol} row")
            buy = self._number(" ".join(cells[1].stripped_strings))
            sell = self._number(" ".join(cells[2].stripped_strings))
            parsed[symbol] = (label, buy, sell)

        missing = REQUIRED_CURRENCIES - parsed.keys()
        if missing:
            raise ValueError(
                "Sarai Shahzada response is incomplete: "
                + ", ".join(sorted(missing))
            )

        rates: list[Rate] = []
        for symbol in DISPLAY_ORDER:
            if symbol not in parsed:
                continue
            label, buy, sell = parsed[symbol]
            scale = BASE_UNIT_SCALES.get(symbol, 1.0)
            unit = (
                f"AFN per {int(scale):,} {symbol}"
                if scale != 1.0
                else f"AFN per {symbol}"
            )
            for rate_type, value in (("cash_buy", buy), ("cash_sell", sell)):
                rates.append(Rate(
                    symbol=f"{symbol}-AFN-{rate_type}",
                    base_currency=symbol,
                    quote_currency="AFN",
                    value=value,
                    source=f"{self.source} ({label})",
                    rate_type=rate_type,
                    timestamp=retrieved_at,
                    rate_direction="quote_per_base",
                    source_unit=unit,
                    retrieved_at=retrieved_at,
                    source_updated_at=None,
                    base_unit_scale=scale,
                ))
        return rates

    @staticmethod
    def _find_rates_table(soup: BeautifulSoup):
        for table in soup.find_all("table"):
            headers = [" ".join(cell.stripped_strings) for cell in table.find_all("th")]
            if len(headers) >= 3 and headers[:3] == ["واحد پول", "خرید", "فروش"]:
                return table
        raise ValueError("Sarai Shahzada rate table was not found")

    @staticmethod
    def _number(value: str) -> float:
        normalized = value.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
        normalized = normalized.replace(",", "").replace("٬", "").strip()
        number = float(normalized)
        if not math.isfinite(number) or number <= 0:
            raise ValueError("Sarai Shahzada returned an invalid rate")
        return number
