import unittest

from utils.conversion import convert_market_amount


class MarketConversionTests(unittest.TestCase):
    def setUp(self):
        self.rates = [
            self._rate("USD", "cash_buy", 70.0),
            self._rate("USD", "cash_sell", 71.0),
            self._rate("EUR", "cash_buy", 75.0),
            self._rate("EUR", "cash_sell", 76.0),
            self._rate("PKR", "cash_buy", 250.0, 1000.0),
            self._rate("PKR", "cash_sell", 260.0, 1000.0),
            self._rate("IRR", "cash_buy", 0.15, 10000.0),
            self._rate("IRR", "cash_sell", 0.16, 10000.0),
            self._rate("AED", "cash_buy", 19.0),
            self._rate("AED", "cash_sell", 20.0),
        ]

    @staticmethod
    def _rate(currency, rate_type, value, scale=1.0, *, stale=False):
        return {
            "rate_key": f"fx:{currency}:AFN:{rate_type}",
            "item_kind": "currency",
            "base_currency": currency,
            "quote_currency": "AFN",
            "rate_type": rate_type,
            "value": value,
            "unit": f"AFN per {scale:g} {currency}",
            "base_unit_scale": scale,
            "source": "Sarai test fixture",
            "source_id": "sarafi_af",
            "retrieved_at": "2026-10-10T10:00:00+00:00",
            "source_updated_at": None,
            "is_manual": False,
            "is_stale": stale,
        }

    def test_buy_and_sell_directions_apply_the_correct_market_side(self):
        received, rate, used = convert_market_amount(
            self.rates, 100.0, "USD", "AFN"
        )
        self.assertEqual((received, rate), (7000.0, 70.0))
        self.assertEqual([item["rate_type"] for item in used], ["cash_buy"])

        paid, rate, used = convert_market_amount(
            self.rates, 7100.0, "AFN", "USD"
        )
        self.assertAlmostEqual(paid, 100.0)
        self.assertAlmostEqual(rate, 1 / 71)
        self.assertEqual([item["rate_type"] for item in used], ["cash_sell"])

    def test_pkr_and_irr_rates_apply_their_source_units_as_raw_currency_units(self):
        afn, effective, _ = convert_market_amount(
            self.rates, 1000.0, "PKR", "AFN"
        )
        self.assertEqual(afn, 250.0)
        self.assertEqual(effective, 0.25)

        afn, effective, _ = convert_market_amount(
            self.rates, 10000.0, "IRR", "AFN"
        )
        self.assertEqual(afn, 0.15)
        self.assertEqual(effective, 0.15 / 10000)

    def test_cross_currency_uses_source_buy_and_target_sell(self):
        result, rate, used = convert_market_amount(
            self.rates, 100.0, "USD", "EUR"
        )
        self.assertAlmostEqual(result, 100 * 70 / 76)
        self.assertEqual(rate, 70 / 76)
        self.assertEqual(
            [item["rate_type"] for item in used],
            ["cash_buy", "cash_sell"],
        )

    def test_invalid_or_stale_rates_never_produce_a_result(self):
        for amount in (0, -1, float("nan"), float("inf")):
            with self.subTest(amount=amount), self.assertRaises(ValueError):
                convert_market_amount(self.rates, amount, "USD", "AFN")

        stale_rates = [
            dict(rate, is_stale=True) if rate["base_currency"] == "USD" else rate
            for rate in self.rates
        ]
        with self.assertRaisesRegex(ValueError, "تازه نیست"):
            convert_market_amount(stale_rates, 1, "USD", "AFN")


if __name__ == "__main__":
    unittest.main()
