import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.environ.setdefault("BOT_TOKEN", "synthetic-test")
os.environ["ADMIN_CHAT_ID"] = "999"

import bot  # noqa: E402
from remittance_store import RemittanceStore  # noqa: E402


def make_amount_update(text):
    message = SimpleNamespace(text=text, photo=None, reply_text=AsyncMock())
    return SimpleNamespace(
        message=message,
        effective_user=SimpleNamespace(id=1),
        effective_message=message,
    )


def make_context(request=None):
    user_data = {"remittance": request} if request else {}
    return SimpleNamespace(user_data=user_data, bot=SimpleNamespace(
        send_message=AsyncMock(),
    ))


class AmountValidationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        bot.REMITTANCE_STORE = RemittanceStore(
            str(Path(self.temp_dir.name) / "amounts.sqlite3")
        )
        bot.REMITTANCE_STORE.initialize()

    def tearDown(self):
        bot.REMITTANCE_STORE = None
        self.temp_dir.cleanup()

    async def _submit(self, text):
        request = {"step": "amount"}
        update = make_amount_update(text)
        context = make_context(request)
        await bot.remittance_message(update, context)
        return context.user_data["remittance"], update.message.reply_text

    async def test_invalid_amounts_are_rejected(self):
        for text in ("nan", "NaN", "inf", "Infinity", "-inf", "-Infinity", "0", "-5", "abc"):
            with self.subTest(text=text):
                request, reply = await self._submit(text)
                self.assertNotIn("amount", request)
                self.assertEqual(request["step"], "amount")
                reply.assert_awaited_once()
                self.assertIn("مبلغ معتبر نیست", reply.await_args.args[0])

    async def test_valid_amount_is_accepted(self):
        for text, expected in (("500", 500.0), ("1,250.5", 1250.5)):
            with self.subTest(text=text):
                request, _ = await self._submit(text)
                self.assertEqual(request["amount"], expected)
                self.assertEqual(request["step"], "currency")


class AdminDecisionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = RemittanceStore(str(Path(self.temp_dir.name) / "bot.sqlite3"))
        self.store.initialize()
        bot.REMITTANCE_STORE = self.store

    def tearDown(self):
        bot.REMITTANCE_STORE = None
        self.temp_dir.cleanup()

    def _new_remittance(self):
        return self.store.create_remittance(
            {
                "sender": "a",
                "recipient": "b",
                "amount": 100.0,
                "currency": "USD",
                "city": "کابل",
                "customer_chat_id": 5,
                "document_file_id": "test-file",
            },
            initial_status=bot.PENDING_REMITTANCE_STATUS,
            commission_rate=bot.REMITS_COMMISSION_RATE,
        )

    async def _click(self, data):
        query = SimpleNamespace(
            data=data,
            from_user=SimpleNamespace(id=999),
            answer=AsyncMock(),
            edit_message_caption=AsyncMock(),
        )
        context = make_context()
        await bot.admin_remittance_callback(SimpleNamespace(callback_query=query), context)
        return query, context

    async def test_pending_request_can_be_approved_or_rejected(self):
        approved = self._new_remittance()
        await self._click(f"admin_approve_{approved['tracking_code']}")
        self.assertEqual(
            self.store.get_remittance(approved["tracking_code"])["status"],
            "✅ تأیید شد",
        )
        rejected = self._new_remittance()
        await self._click(f"admin_reject_{rejected['tracking_code']}")
        self.assertEqual(
            self.store.get_remittance(rejected["tracking_code"])["status"],
            "❌ رد شد",
        )

    async def test_decided_request_is_not_changed_by_stale_buttons(self):
        for decided, stale_click in (
            ("❌ رد شد", "admin_approve_LA-1"),
            ("✅ تأیید شد", "admin_reject_LA-1"),
            ("✅ تأیید شد", "admin_approve_LA-1"),
            ("⚙️ در حال پردازش", "admin_approve_LA-1"),
            ("❌ رد شد", "admin_reject_LA-1"),
        ):
            with self.subTest(decided=decided, click=stale_click):
                request = self._new_remittance()
                self.store.transition_status(
                    request["tracking_code"],
                    expected_statuses={bot.PENDING_REMITTANCE_STATUS},
                    new_status=decided,
                )
                action = stale_click.split("_")[1]
                query, context = await self._click(
                    f"admin_{action}_{request['tracking_code']}"
                )
                self.assertEqual(
                    self.store.get_remittance(request["tracking_code"])["status"],
                    decided,
                )
                context.bot.send_message.assert_not_awaited()
                query.edit_message_caption.assert_not_awaited()
                query.answer.assert_awaited_once()
                self.assertTrue(query.answer.await_args.kwargs.get("show_alert"))
                self.assertIn("قبلاً بررسی شده", query.answer.await_args.args[0])

    async def test_confirmation_is_persisted_before_success_is_reported(self):
        remittance = {
            "step": "summary",
            "sender": "فرستنده",
            "recipient": "گیرنده",
            "amount": 100.0,
            "currency": "USD",
            "city": "کابل",
            "document_file_id": "test-file",
        }
        user_data = {"remittance": remittance}
        query = SimpleNamespace(
            from_user=SimpleNamespace(id=5),
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(
            user_data=user_data,
            bot=SimpleNamespace(send_photo=AsyncMock()),
        )

        await bot.confirm_remittance(SimpleNamespace(callback_query=query), context)

        stored = self.store.get_customer_remittances(5)
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["status"], bot.PENDING_REMITTANCE_STATUS)
        self.assertEqual(stored[0]["document_file_id"], "test-file")
        self.assertNotIn("remittance", user_data)
        query.edit_message_text.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
