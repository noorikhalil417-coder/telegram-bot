import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

os.environ.setdefault("BOT_TOKEN", "synthetic-test")
os.environ["ADMIN_CHAT_ID"] = "999"

import bot  # noqa: E402
from remittance_store import RemittanceStore  # noqa: E402
from services.managed_rates import (  # noqa: E402
    DAB_REFERENCE_SOURCE_ID,
    REFERENCE_SOURCE_ID,
    SARAI_SOURCE_ID,
    ManagedRatesService,
)
from services.rates import RatesService  # noqa: E402
from config import Settings  # noqa: E402
from models import ProviderResult, Rate  # noqa: E402
from services.exchange_rate_api import ExchangeRateAPIProvider  # noqa: E402
from services.gold import GoldProvider  # noqa: E402
from services.frankfurter import FrankfurterProvider  # noqa: E402
from utils.formatting import format_managed_rates  # noqa: E402


class RateStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = RemittanceStore(str(Path(self.temp_dir.name) / "rates.sqlite3"))
        self.store.initialize()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _automatic_rate(self, value=65.0):
        self.store.upsert_automatic_rates(REFERENCE_SOURCE_ID, [{
            "rate_key": self.store.fx_rate_key("USD", "AFN", "mid_market"),
            "item_kind": "currency",
            "base_currency": "USD",
            "quote_currency": "AFN",
            "rate_type": "mid_market",
            "value": value,
            "unit": "AFN per USD",
            "purity": None,
            "source": "Reference API",
            "source_updated_at": "2026-10-10T00:00:00+00:00",
            "retrieved_at": "2026-10-10T01:00:00+00:00",
        }])

    def test_manual_override_wins_automatic_update_and_restore_reveals_auto(self):
        key = self.store.fx_rate_key("USD", "AFN", "mid_market")
        self._automatic_rate(65.0)
        self.store.set_manual_rate(
            rate_key=key,
            item_kind="currency",
            base_currency="USD",
            quote_currency="AFN",
            rate_type="mid_market",
            value=68.0,
            unit="AFN per USD",
            purity=None,
            administrator_id=999,
        )
        self._automatic_rate(66.0)
        effective = self.store.list_effective_rates()
        self.assertEqual(len(effective), 1)
        self.assertTrue(effective[0]["is_manual"])
        self.assertEqual(effective[0]["value"], 68.0)

        self.assertTrue(self.store.restore_automatic_rate(key, 999))
        restored = self.store.list_effective_rates()
        self.assertFalse(restored[0]["is_manual"])
        self.assertEqual(restored[0]["value"], 66.0)

    def test_manual_rate_add_and_edit_are_atomic_and_audited(self):
        key = self.store.fx_rate_key("EUR", "AFN", "cash_buy")
        first_action = self.store.set_manual_rate(
            rate_key=key,
            item_kind="currency",
            base_currency="EUR",
            quote_currency="AFN",
            rate_type="cash_buy",
            value=70.0,
            unit="AFN per EUR",
            purity=None,
            administrator_id=999,
        )
        second_action = self.store.set_manual_rate(
            rate_key=key,
            item_kind="currency",
            base_currency="EUR",
            quote_currency="AFN",
            rate_type="cash_buy",
            value=71.0,
            unit="AFN per EUR",
            purity=None,
            administrator_id=1000,
        )
        audit = self.store.get_rate_audit_history()

        self.assertEqual((first_action, second_action), ("manual_add", "manual_edit"))
        self.assertEqual(len(audit), 2)
        self.assertEqual(audit[0]["administrator_id"], 1000)
        self.assertEqual(audit[0]["action"], "manual_edit")
        self.assertIn('"value": 70.0', audit[0]["old_value_json"])
        self.assertIn('"value": 71.0', audit[0]["new_value_json"])
        self.assertTrue(audit[0]["changed_at"])

    def test_manual_gold_pair_persists_after_storage_restart(self):
        key = self.store.gold_rate_key("gram", "AFN", "21K")
        self.store.set_manual_rate(
            rate_key=key,
            item_kind="gold",
            base_currency="XAU",
            quote_currency="AFN",
            rate_type="local_market_manual",
            value=8500.0,
            unit="gram",
            purity="21K",
            administrator_id=999,
        )

        reopened = RemittanceStore(self.store.database_path)
        reopened.initialize()
        rate = reopened.list_effective_rates()[0]
        self.assertEqual(rate["value"], 8500.0)
        self.assertEqual(rate["unit"], "gram")
        self.assertEqual(rate["quote_currency"], "AFN")
        self.assertEqual(rate["purity"], "21K")
        self.assertTrue(rate["is_manual"])

    def test_invalid_manual_and_automatic_numbers_are_rejected(self):
        for value in (0, -1, float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.store.set_manual_rate(
                        rate_key="fx:USD:AFN:spot",
                        item_kind="currency",
                        base_currency="USD",
                        quote_currency="AFN",
                        rate_type="spot",
                        value=value,
                        unit="AFN per USD",
                        purity=None,
                        administrator_id=999,
                    )
                with self.assertRaises(ValueError):
                    self.store.upsert_automatic_rates(REFERENCE_SOURCE_ID, [{
                        "rate_key": "fx:USD:AFN:spot",
                        "item_kind": "currency",
                        "base_currency": "USD",
                        "quote_currency": "AFN",
                        "rate_type": "spot",
                        "value": value,
                        "unit": "AFN per USD",
                        "source": "Reference API",
                        "retrieved_at": datetime.now(timezone.utc).isoformat(),
                    }])

    def test_source_toggle_persists_and_is_audited(self):
        self.assertTrue(self.store.is_source_enabled(DAB_REFERENCE_SOURCE_ID))
        self.store.set_source_enabled(DAB_REFERENCE_SOURCE_ID, False, 999)
        reopened = RemittanceStore(self.store.database_path)
        reopened.initialize()
        self.assertFalse(reopened.is_source_enabled(DAB_REFERENCE_SOURCE_ID))
        audit = reopened.get_rate_audit_history()
        self.assertEqual(audit[0]["action"], "source_toggle")
        self.assertEqual(audit[0]["administrator_id"], 999)

    def test_effective_rate_presentation_shows_provenance_staleness_and_unit(self):
        self._automatic_rate()
        rate = self.store.list_effective_rates()[0]
        rate["is_stale"] = True
        text = format_managed_rates([rate])
        self.assertIn("Reference API", text)
        self.assertIn("AFN per USD", text)
        self.assertIn("نرخ مرجع روزانه", text)
        self.assertIn("دادهٔ ذخیره‌شده و قدیمی", text)


class AutomaticRateServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = RemittanceStore(str(Path(self.temp_dir.name) / "rates.sqlite3"))
        self.store.initialize()
        self.rates = RatesService(Settings(bot_token="synthetic-test"))
        self.managed = ManagedRatesService(self.store, self.rates)

    async def asyncTearDown(self):
        self.temp_dir.cleanup()

    async def test_provider_failure_preserves_cached_value_and_formats_it_stale(self):
        self.store.upsert_automatic_rates(SARAI_SOURCE_ID, [{
            "rate_key": self.store.fx_rate_key("USD", "AFN", "cash_buy"),
            "item_kind": "currency",
            "base_currency": "USD",
            "quote_currency": "AFN",
            "rate_type": "cash_buy",
            "value": 65.0,
            "unit": "AFN per USD",
            "source": "Sarai Shahzada (sarafi.af)",
            "retrieved_at": "2020-01-01T00:00:00+00:00",
        }])
        self.rates.refresh_sarai_rates = AsyncMock(
            return_value=ProviderResult(available=False, message="offline")
        )

        values = await self.managed.get_effective_rates()

        usd_buy = next(rate for rate in values if rate["rate_key"] == self.store.fx_rate_key(
            "USD", "AFN", "cash_buy"
        ))
        self.assertEqual(usd_buy["value"], 65.0)
        self.assertTrue(usd_buy["is_stale"])
        self.assertIn("دادهٔ ذخیره‌شده و قدیمی", format_managed_rates(values))

    async def test_automatic_provider_rate_is_persisted_and_manual_override_survives_refresh(self):
        now = datetime.now(timezone.utc)
        rate = Rate(
            "USDAFN-cash_buy", "USD", "AFN", 65.123, "Sarai Shahzada (sarafi.af)", "cash_buy",
            now, rate_direction="quote_per_base", retrieved_at=now,
            source_updated_at=now,
        )
        self.rates.refresh_sarai_rates = AsyncMock(
            return_value=ProviderResult(rates=(rate,))
        )
        self.assertTrue(await self.managed.refresh_sarai(force=True))
        key = self.store.fx_rate_key("USD", "AFN", "cash_buy")
        self.store.set_manual_rate(
            rate_key=key,
            item_kind="currency",
            base_currency="USD",
            quote_currency="AFN",
            rate_type="cash_buy",
            value=70.0,
            unit="AFN per USD",
            purity=None,
            administrator_id=999,
        )
        rate_new = Rate(
            "USDAFN-cash_buy", "USD", "AFN", 66.0, "Sarai Shahzada (sarafi.af)", "cash_buy",
            now, rate_direction="quote_per_base", retrieved_at=now,
            source_updated_at=now,
        )
        self.rates.refresh_sarai_rates = AsyncMock(
            return_value=ProviderResult(rates=(rate_new,))
        )
        await self.managed.refresh_sarai(force=True)
        effective = await self.managed.get_effective_rates()
        usd_buy = next(rate for rate in effective if rate["rate_key"] == key)
        self.assertEqual(usd_buy["value"], 70.0)
        self.assertTrue(usd_buy["is_manual"])

    async def test_disabled_provider_is_not_requested(self):
        self.store.set_source_enabled(SARAI_SOURCE_ID, False, 999)
        self.rates.refresh_sarai_rates = AsyncMock()
        await self.managed.refresh_sarai(force=True)
        self.rates.refresh_sarai_rates.assert_not_awaited()

    async def test_recent_provider_failure_is_throttled_for_customer_requests(self):
        self.rates.refresh_sarai_rates = AsyncMock(
            return_value=ProviderResult(available=False, message="offline")
        )
        self.assertFalse(await self.managed.refresh_sarai(force=True))
        await self.managed.get_effective_rates()
        self.rates.refresh_sarai_rates.assert_awaited_once()

    async def test_gold_remains_manual_only_until_automatic_unit_is_documented(self):
        result = await self.managed.get_effective_gold()
        self.assertEqual(result, [])
        self.rates.refresh_gold = AsyncMock()
        await self.managed.refresh_gold(force=True)
        self.rates.refresh_gold.assert_not_awaited()


class ProviderFailureTests(unittest.TestCase):
    def test_reference_provider_retries_timeout_and_returns_unavailable(self):
        import requests

        provider = ExchangeRateAPIProvider(timeout=0.01, retries=2)
        with patch(
            "services.exchange_rate_api.requests.get",
            side_effect=requests.Timeout("timed out"),
        ) as get:
            result = provider.fetch()
        self.assertFalse(result.available)
        self.assertEqual(get.call_count, 3)

    def test_gold_provider_reports_timeout_without_claiming_live_price(self):
        result = GoldProvider().fetch()
        self.assertFalse(result.available)
        self.assertIsNone(result.gold)
        self.assertIn("واحد قیمت", result.message)

    def test_frankfurter_parses_documented_rows_and_preserves_irr_units(self):
        response = SimpleNamespace(
            json=lambda: [
                {"date": "2026-10-10", "base": "USD", "quote": "AFN", "rate": 64.855},
                {"date": "2026-10-10", "base": "USD", "quote": "IRR", "rate": 1748968},
            ],
            raise_for_status=Mock(),
        )
        with patch("services.frankfurter.requests.get", return_value=response) as get:
            result = FrankfurterProvider()._fetch(
                "https://api.frankfurter.dev/v2/rates",
                params={"base": "USD", "quotes": "AFN,IRR"},
                source="Frankfurter test",
                rate_type="mid_market",
                expected_quotes={"AFN", "IRR"},
            )
        self.assertTrue(result.available)
        indexed = {rate.quote_currency: rate for rate in result.rates}
        self.assertEqual(indexed["AFN"].value, 64.855)
        self.assertEqual(indexed["IRR"].source_unit, "IRR (rial; not converted to toman)")
        self.assertEqual(indexed["IRR"].rate_direction, "quote_per_base")
        self.assertEqual(indexed["AFN"].source_updated_at.date().isoformat(), "2026-10-10")
        get.assert_called_once()

    def test_frankfurter_timeout_retries_and_reports_unavailable(self):
        import requests

        provider = FrankfurterProvider(timeout=0.01, retries=2)
        with patch(
            "services.frankfurter.requests.get",
            side_effect=requests.Timeout("timed out"),
        ) as get:
            result = provider.fetch()
        self.assertFalse(result.available)
        self.assertEqual(get.call_count, 3)


class RateAdminAccessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        bot.REMITTANCE_STORE = RemittanceStore(
            str(Path(self.temp_dir.name) / "admin.sqlite3")
        )
        bot.REMITTANCE_STORE.initialize()

    async def asyncTearDown(self):
        bot.REMITTANCE_STORE = None
        self.temp_dir.cleanup()

    async def test_unauthorized_panel_callback_does_not_show_panel_or_touch_sources(self):
        query = SimpleNamespace(
            data="admin_rates_view",
            from_user=SimpleNamespace(id=123),
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        await bot.rate_management_callback(
            SimpleNamespace(callback_query=query),
            SimpleNamespace(user_data={}),
        )
        query.answer.assert_awaited_once_with("دسترسی مجاز نیست.", show_alert=True)
        query.edit_message_text.assert_not_awaited()

    async def test_authorized_user_can_open_panel_and_add_currency_rate(self):
        query = SimpleNamespace(
            data="admin_rates_add_currency",
            from_user=SimpleNamespace(id=999),
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(user_data={})
        await bot.rate_management_callback(
            SimpleNamespace(callback_query=query),
            context,
        )
        self.assertEqual(context.user_data["rate_management_input"], "currency")

        message = SimpleNamespace(
            text="USD AFN cash_buy 65.5 AFN per USD",
            reply_text=AsyncMock(),
        )
        await bot.handle_rate_management_input(
            SimpleNamespace(
                effective_user=SimpleNamespace(id=999),
                effective_message=message,
            ),
            context,
        )
        self.assertEqual(bot.REMITTANCE_STORE.list_effective_rates(), [])
        confirm = SimpleNamespace(
            from_user=SimpleNamespace(id=999),
            edit_message_text=AsyncMock(),
        )
        await bot.confirm_rate_management_input(
            SimpleNamespace(callback_query=confirm), context
        )
        stored = bot.REMITTANCE_STORE.list_effective_rates()
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["value"], 65.5)
        self.assertTrue(stored[0]["is_manual"])
        self.assertEqual(bot.REMITTANCE_STORE.get_rate_audit_history()[0]["administrator_id"], 999)

        context.user_data["rate_management_input"] = "currency"
        edit_message = SimpleNamespace(
            text="USD AFN cash_buy 66 AFN per USD",
            reply_text=AsyncMock(),
        )
        await bot.handle_rate_management_input(
            SimpleNamespace(
                effective_user=SimpleNamespace(id=999),
                effective_message=edit_message,
            ),
            context,
        )
        confirm_edit = SimpleNamespace(
            from_user=SimpleNamespace(id=999),
            edit_message_text=AsyncMock(),
        )
        await bot.confirm_rate_management_input(
            SimpleNamespace(callback_query=confirm_edit), context
        )
        self.assertEqual(bot.REMITTANCE_STORE.list_effective_rates()[0]["value"], 66.0)
        audit = bot.REMITTANCE_STORE.get_rate_audit_history()
        self.assertEqual(audit[0]["action"], "manual_edit")

    async def test_authorized_admin_can_add_gold_and_restore_manual_override(self):
        context = SimpleNamespace(user_data={"rate_management_input": "gold"})
        gold_message = SimpleNamespace(
            text="8500 AFN gram 21K SOURCE=بازار_معتبر",
            reply_text=AsyncMock(),
        )
        await bot.handle_rate_management_input(
            SimpleNamespace(
                effective_user=SimpleNamespace(id=999),
                effective_message=gold_message,
            ),
            context,
        )
        confirm = SimpleNamespace(
            from_user=SimpleNamespace(id=999),
            edit_message_text=AsyncMock(),
        )
        await bot.confirm_rate_management_input(
            SimpleNamespace(callback_query=confirm), context
        )
        gold = bot.REMITTANCE_STORE.list_effective_rates()[0]
        self.assertEqual(gold["item_kind"], "gold")
        self.assertEqual(gold["unit"], "gram")
        self.assertEqual(gold["purity"], "21K")
        self.assertEqual(gold["source"], "بازار معتبر")

        key = bot.REMITTANCE_STORE.gold_rate_key("gram", "AFN", "21K")
        bot.REMITTANCE_STORE.upsert_automatic_rates(SARAI_SOURCE_ID, [{
            "rate_key": key,
            "item_kind": "gold",
            "base_currency": "XAU",
            "quote_currency": "AFN",
            "rate_type": "spot",
            "value": 8000,
            "unit": "gram",
            "purity": "21K",
            "source": "test source",
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
        }])
        context.user_data["rate_management_input"] = "restore"
        restore_message = SimpleNamespace(
            text="GOLD gram AFN 21K",
            reply_text=AsyncMock(),
        )
        await bot.handle_rate_management_input(
            SimpleNamespace(
                effective_user=SimpleNamespace(id=999),
                effective_message=restore_message,
            ),
            context,
        )
        effective = bot.REMITTANCE_STORE.list_effective_rates()
        self.assertEqual(effective[0]["value"], 8000)
        self.assertFalse(effective[0]["is_manual"])

    async def test_unauthorized_rate_input_is_rejected(self):
        context = SimpleNamespace(user_data={"rate_management_input": "currency"})
        message = SimpleNamespace(text="USD AFN cash_buy 65", reply_text=AsyncMock())
        await bot.handle_rate_management_input(
            SimpleNamespace(
                effective_user=SimpleNamespace(id=123),
                effective_message=message,
            ),
            context,
        )
        self.assertNotIn("rate_management_input", context.user_data)
        self.assertIn("دسترسی مجاز نیست", message.reply_text.await_args.args[0])

    async def test_invalid_rates_are_rejected_before_a_preview_is_staged(self):
        for value in ("0", "-1", "nan", "inf", "-inf", "1e999"):
            with self.subTest(value=value):
                context = SimpleNamespace(user_data={"rate_management_input": "currency"})
                message = SimpleNamespace(
                    text=f"USD AFN cash_buy {value}",
                    reply_text=AsyncMock(),
                )
                await bot.handle_rate_management_input(
                    SimpleNamespace(
                        effective_user=SimpleNamespace(id=999),
                        effective_message=message,
                    ),
                    context,
                )
                self.assertNotIn("pending_rate_change", context.user_data)
                self.assertEqual(bot.REMITTANCE_STORE.list_effective_rates(), [])

    async def test_confirmation_rechecks_admin_authorization(self):
        context = SimpleNamespace(user_data={
            "pending_rate_change": {
                "operation": "currency",
                "rate_key": "fx:USD:AFN:cash_buy",
                "base_currency": "USD",
                "quote_currency": "AFN",
                "rate_type": "cash_buy",
                "value": 65.0,
                "unit": "AFN per USD",
            }
        })
        query = SimpleNamespace(
            from_user=SimpleNamespace(id=123),
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        await bot.confirm_rate_management_input(
            SimpleNamespace(callback_query=query), context
        )
        query.answer.assert_awaited_once_with("دسترسی مجاز نیست.", show_alert=True)
        self.assertEqual(bot.REMITTANCE_STORE.list_effective_rates(), [])

    async def test_automatic_rate_adjustment_is_previewed_then_audited(self):
        now = datetime.now(timezone.utc)
        self.store_rate = bot.REMITTANCE_STORE
        self.store_rate.upsert_automatic_rates(SARAI_SOURCE_ID, [{
            "rate_key": self.store_rate.fx_rate_key("USD", "AFN", "cash_buy"),
            "item_kind": "currency",
            "base_currency": "USD",
            "quote_currency": "AFN",
            "rate_type": "cash_buy",
            "value": 65.0,
            "unit": "AFN per USD",
            "source": "Sarai test fixture",
            "retrieved_at": now.isoformat(),
        }])
        context = SimpleNamespace(user_data={"rate_management_input": "adjustment"})
        message = SimpleNamespace(
            text="USD AFN cash_buy 1",
            reply_text=AsyncMock(),
        )
        await bot.handle_rate_management_input(
            SimpleNamespace(
                effective_user=SimpleNamespace(id=999),
                effective_message=message,
            ),
            context,
        )
        self.assertIsNone(
            self.store_rate.get_rate(
                self.store_rate.fx_rate_key("USD", "AFN", "cash_buy")
            )["adjustment"]
        )
        query = SimpleNamespace(
            from_user=SimpleNamespace(id=999),
            edit_message_text=AsyncMock(),
        )
        await bot.confirm_rate_management_input(
            SimpleNamespace(callback_query=query), context
        )
        rate = self.store_rate.get_rate(
            self.store_rate.fx_rate_key("USD", "AFN", "cash_buy")
        )
        self.assertEqual(rate["value"], 66.0)
        self.assertTrue(rate["is_adjusted"])
        self.assertEqual(
            self.store_rate.get_rate_audit_history()[0]["action"],
            "adjustment_set",
        )


if __name__ == "__main__":
    unittest.main()
