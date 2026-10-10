import unittest
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from config import Settings
from models import GoldPrice, ProviderResult, Rate
from services.exchange_rate_api import ExchangeRateAPIProvider
from services.gold import GoldProvider
from services.rates import RatesService
from services.sarai_shahzada import SaraiShahzadaProvider
from services.xe import XEProvider
from utils.formatting import (
    format_comparison,
    format_gold,
    format_reference_rates,
    format_xe_rates,
)


class RateProviderContractTests(unittest.TestCase):
    def setUp(self):
        self.retrieved_at = datetime(2026, 10, 2, 18, 0, tzinfo=timezone.utc)
        self.settings = Settings(
            bot_token="synthetic-test",
            eur_deduction=0.50,
            other_currency_deduction=50.0,
        )
        self.sarai_html = """<table>
        <tr><th>واحد پول</th><th>خرید</th><th>فروش</th></tr>
        <tr><td>USD - دالر آمریکا</td><td>0.256</td><td>65.05</td></tr>
        <tr><td>EUR - یورو اروپا</td><td>74.00</td><td>74.20</td></tr>
        <tr><td>IRR - تومان ایران هزار</td><td>0.25</td><td>0.26</td></tr>
        </table>"""

    def test_sarai_keeps_raw_values_and_unknown_direction(self):
        rates = SaraiShahzadaProvider(self.settings)._parse(
            self.sarai_html,
            retrieved_at=self.retrieved_at,
        )
        indexed = {(rate.quote_currency, rate.rate_type): rate for rate in rates}

        self.assertEqual(indexed[("USD", "cash_buy")].value, 0.256)
        self.assertEqual(indexed[("EUR", "cash_buy")].value, 74.0)
        self.assertEqual(indexed[("EUR", "cash_sell")].value, 74.2)
        toman = indexed[("IRR", "cash_buy")]
        self.assertEqual(toman.value, 0.25)
        self.assertEqual(toman.source_unit, "IRR - تومان ایران هزار")
        self.assertEqual(toman.rate_direction, "source_unspecified")
        self.assertIsNone(toman.source_updated_at)
        self.assertEqual(toman.retrieved_at, self.retrieved_at)

    def test_exchange_rate_api_declares_quote_per_base_and_both_times(self):
        payload = {
            "result": "success",
            "time_last_update_utc": "Fri, 02 Oct 2026 18:00:00 +0000",
            "time_next_update_utc": "Sat, 03 Oct 2026 18:00:00 +0000",
            "rates": {"AFN": 65.0, "EUR": 0.9, "GBP": 0.8, "PKR": 280.0, "INR": 83.0, "IRR": 42000.0},
        }
        result = ExchangeRateAPIProvider()._parse(payload, retrieved_at=self.retrieved_at)
        rate = result.rates[0]
        self.assertEqual(rate.rate_direction, "quote_per_base")
        self.assertEqual(rate.value, 65.0)
        self.assertEqual(rate.retrieved_at, self.retrieved_at)
        self.assertIsNotNone(rate.source_updated_at)

    def test_xe_provider_declares_quote_per_base_without_rounding_value(self):
        response = Mock()
        response.json.return_value = {
            "to": [{"quotecurrency": "AFN", "mid": 0.123456789}]
        }
        response.raise_for_status.return_value = None
        with patch("services.xe.requests.get", return_value=response):
            result = XEProvider("synthetic-key", "synthetic-account").fetch()
        rate = result.rates[0]
        self.assertEqual(rate.value, 0.123456789)
        self.assertEqual(rate.rate_direction, "quote_per_base")
        self.assertEqual(rate.retrieved_at, rate.timestamp)
        self.assertIsNone(rate.source_updated_at)

    def test_formatting_preserves_precision_source_unit_and_staleness(self):
        rates = SaraiShahzadaProvider(self.settings)._parse(
            self.sarai_html,
            retrieved_at=self.retrieved_at,
        )
        message = format_reference_rates(tuple(rates))
        self.assertIn("خرید (طبق منبع): 0.256", message)
        self.assertIn("فروش (طبق منبع): 65.05", message)
        self.assertIn("تومان ایران (واحد منبع: هزار تومان)", message)
        self.assertIn("خرید (طبق منبع): 0.25", message)
        self.assertNotIn("AFN", message)
        self.assertIn("جهت ریاضی نرخ در منبع مشخص نشده", message)
        self.assertIn("بروزرسانی منبع: اعلام نشده", message)
        self.assertIn("📌 منبع: Sarai Shahzada (sarafi.af)", message)

        stale_rates = tuple(replace(rate, is_stale=True) for rate in rates)
        self.assertIn("cache قدیمی است و تازه نیست", format_reference_rates(stale_rates))

    def test_comparison_uses_provider_source_name(self):
        rate = Rate(
            symbol="AFN-USD-cash_buy",
            base_currency="AFN",
            quote_currency="USD",
            value=65.0,
            source="Sarai Shahzada (sarafi.af) (USD - دالر آمریکا)",
            rate_type="cash_buy",
            timestamp=self.retrieved_at,
            rate_direction="source_unspecified",
        )
        message = format_comparison((rate,), (), False)
        self.assertIn("Sarai Shahzada (sarafi.af)", message)
        self.assertNotIn("ExchangeRate-API (reference): 1", message)

    def test_xe_formatter_does_not_call_retrieval_time_source_update(self):
        rate = Rate(
            symbol="USDAFN",
            base_currency="USD",
            quote_currency="AFN",
            value=65.0,
            source="XE (mid-market)",
            rate_type="mid_market",
            timestamp=self.retrieved_at,
            rate_direction="quote_per_base",
            source_unit="AFN",
            retrieved_at=self.retrieved_at,
        )
        message = format_xe_rates((rate,))
        self.assertIn("0.123456789 AFN", format_xe_rates((replace(rate, value=0.123456789),)))
        self.assertIn("زمان دریافت: 2026-10-02 22:30", message)
        self.assertIn("بروزرسانی منبع: اعلام نشده", message)

        stale_rate = replace(rate, is_stale=True)
        self.assertIn("cache قدیمی است و تازه نیست", format_xe_rates((stale_rate,)))

        inverse_rate = replace(rate, rate_direction="base_per_quote", value=0.25)
        self.assertIn("1 AFN = 0.25 USD", format_xe_rates((inverse_rate,)))
        unspecified_rate = replace(
            rate,
            rate_direction="source_unspecified",
            rate_type="cash_buy",
            value=0.256,
        )
        self.assertIn("XE (mid-market) [cash_buy]: 0.256", format_xe_rates((unspecified_rate,)))

    def test_comparison_formats_each_explicit_direction_without_precision_loss(self):
        quote_per_base = Rate(
            "USDAFN", "USD", "AFN", 0.123456789, "XE", "mid_market",
            self.retrieved_at, rate_direction="quote_per_base",
        )
        base_per_quote = Rate(
            "USDAFN", "USD", "AFN", 0.123456789, "Verified inverse source", "spot",
            self.retrieved_at, rate_direction="base_per_quote",
        )
        message = format_comparison((quote_per_base, base_per_quote), (), True)
        self.assertIn("1 USD = 0.123456789 AFN", message)
        self.assertIn("1 AFN = 0.123456789 USD", message)

    def test_gold_displays_source_update_separately_from_retrieval(self):
        result = GoldProvider().fetch()
        self.assertFalse(result.available)
        self.assertIsNone(result.gold)
        self.assertIn("واحد قیمت", result.message)

        manually_recorded = GoldPrice(
            "XAU", 4000, 128.6, "مدیریت دستی", self.retrieved_at,
            retrieved_at=self.retrieved_at,
        )
        formatted = format_gold(manually_recorded)
        self.assertIn("2026-10-02T18:00:00+00:00", formatted)
        self.assertIn("🕐 زمان دریافت:", formatted)


class IndependentRefreshTests(unittest.IsolatedAsyncioTestCase):
    def test_stale_copy_preserves_rate_contract_metadata(self):
        timestamp = datetime(2026, 10, 2, tzinfo=timezone.utc)
        retrieved = timestamp.replace(hour=1)
        rate = Rate(
            "ir",
            "AFN",
            "IRR",
            0.25,
            "Sarai source",
            "cash_buy",
            timestamp,
            rate_direction="source_unspecified",
            source_unit="IRR - Toman thousand",
            retrieved_at=retrieved,
            source_updated_at=None,
        )
        stale = RatesService._stale(ProviderResult(rates=(rate,)), "offline").rates[0]
        self.assertTrue(stale.is_stale)
        self.assertEqual(stale.value, rate.value)
        self.assertEqual(stale.rate_direction, rate.rate_direction)
        self.assertEqual(stale.source_unit, rate.source_unit)
        self.assertEqual(stale.retrieved_at, retrieved)

    async def test_reference_and_gold_refresh_call_only_their_provider(self):
        service = RatesService(Settings(bot_token="synthetic-test"))
        timestamp = datetime(2026, 10, 2, tzinfo=timezone.utc)
        rate = Rate("USD", "USD", "AFN", 65.0, "test", "mid_market", timestamp)
        gold = GoldPrice("XAU", 4000, 128.6, "test", timestamp)
        service.frankfurter_provider.fetch = Mock(return_value=ProviderResult(rates=(rate,)))
        service.gold_provider.fetch = Mock(return_value=ProviderResult(gold=gold))

        reference = await service.refresh_reference_rates()
        self.assertEqual(reference.rates, (rate,))
        service.frankfurter_provider.fetch.assert_called_once()
        service.gold_provider.fetch.assert_not_called()

        refreshed_gold = await service.refresh_gold()
        self.assertEqual(refreshed_gold.gold, gold)
        service.frankfurter_provider.fetch.assert_called_once()
        service.gold_provider.fetch.assert_called_once()


if __name__ == "__main__":
    unittest.main()
