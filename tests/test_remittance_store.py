import sqlite3
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from config import load_settings
from remittance_store import RemittanceStore


PENDING = "🕐 در انتظار بررسی"


class RemittanceStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = str(Path(self.temp_dir.name) / "data" / "bot.sqlite3")
        self.store = RemittanceStore(self.database_path)
        self.store.initialize()

    def tearDown(self):
        self.temp_dir.cleanup()

    def make_request(self, customer_chat_id=123):
        return {
            "sender": "فرستنده آزمایشی",
            "recipient": "گیرنده آزمایشی",
            "amount": 250.0,
            "currency": "EUR",
            "city": "کابل",
            "customer_chat_id": customer_chat_id,
            "document_file_id": "telegram-file-id",
        }

    def test_remittance_is_saved_and_restored_after_reopening_database(self):
        saved = self.store.create_remittance(
            self.make_request(),
            initial_status=PENDING,
            commission_rate=0.04,
        )

        reopened = RemittanceStore(self.database_path)
        reopened.initialize()
        restored = reopened.get_remittance(saved["tracking_code"])

        self.assertEqual(restored["sender"], "فرستنده آزمایشی")
        self.assertEqual(restored["recipient"], "گیرنده آزمایشی")
        self.assertEqual(restored["amount"], 250.0)
        self.assertEqual(restored["currency"], "EUR")
        self.assertEqual(restored["city"], "کابل")
        self.assertEqual(restored["document_file_id"], "telegram-file-id")
        self.assertEqual(restored["customer_chat_id"], 123)
        self.assertEqual(restored["commission"], 10.0)
        self.assertEqual(restored["total"], 260.0)
        self.assertEqual(restored["status"], PENDING)
        self.assertTrue(restored["created_at"])
        self.assertEqual(restored["created_at"], restored["status_updated_at"])

    def test_status_and_status_history_survive_store_restart(self):
        saved = self.store.create_remittance(
            self.make_request(),
            initial_status=PENDING,
            commission_rate=0.04,
        )
        approved = self.store.transition_status(
            saved["tracking_code"],
            expected_statuses={PENDING},
            new_status="✅ تأیید شد",
        )
        self.assertIsNotNone(approved)

        reopened = RemittanceStore(self.database_path)
        reopened.initialize()
        restored = reopened.get_remittance(saved["tracking_code"])
        history = reopened.get_status_history(saved["tracking_code"])

        self.assertEqual(restored["status"], "✅ تأیید شد")
        self.assertGreaterEqual(restored["status_updated_at"], restored["created_at"])
        self.assertEqual([item["status"] for item in history], [PENDING, "✅ تأیید شد"])

    def test_draft_survives_restart_and_is_removed_atomically_on_submission(self):
        draft = {
            "step": "summary",
            "sender": "فرستنده",
            "recipient": "گیرنده",
            "amount": 75.0,
            "currency": "USD",
            "city": "هرات",
            "document_file_id": "document-id",
        }
        self.store.save_draft(321, draft)
        reopened = RemittanceStore(self.database_path)
        reopened.initialize()
        self.assertEqual(reopened.get_draft(321), draft)

        submitted = dict(draft, customer_chat_id=321)
        remittance = reopened.create_remittance(
            submitted,
            initial_status=PENDING,
            commission_rate=0.04,
        )
        self.assertEqual(remittance["document_file_id"], "document-id")
        self.assertIsNone(reopened.get_draft(321))

    def test_tracking_codes_and_customer_codes_are_unique_and_persistent(self):
        first_customer_code = self.store.get_or_create_customer_code(123)
        self.assertEqual(self.store.get_or_create_customer_code(123), first_customer_code)
        second_customer_code = self.store.get_or_create_customer_code(456)
        self.assertNotEqual(first_customer_code, second_customer_code)

        first = self.store.create_remittance(
            self.make_request(123),
            initial_status=PENDING,
            commission_rate=0.04,
        )
        reopened = RemittanceStore(self.database_path)
        reopened.initialize()
        third_customer_code = reopened.get_or_create_customer_code(789)
        second = reopened.create_remittance(
            self.make_request(456),
            initial_status=PENDING,
            commission_rate=0.04,
        )

        self.assertEqual(len({first_customer_code, second_customer_code, third_customer_code}), 3)
        self.assertNotEqual(first["tracking_code"], second["tracking_code"])
        self.assertEqual(
            reopened.get_or_create_customer_code(123),
            first_customer_code,
        )

    def test_only_one_concurrent_status_decision_succeeds(self):
        saved = self.store.create_remittance(
            self.make_request(),
            initial_status=PENDING,
            commission_rate=0.04,
        )

        def decide(status):
            return self.store.transition_status(
                saved["tracking_code"],
                expected_statuses={PENDING},
                new_status=status,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(decide, ("✅ تأیید شد", "❌ رد شد")))

        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertIn(
            self.store.get_remittance(saved["tracking_code"])["status"],
            {"✅ تأیید شد", "❌ رد شد"},
        )

    def test_database_connection_errors_are_reported(self):
        with patch(
            "remittance_store.sqlite3.connect",
            side_effect=sqlite3.OperationalError("disk unavailable"),
        ):
            with self.assertLogs("remittance_store", level="ERROR"):
                with self.assertRaises(sqlite3.OperationalError):
                    RemittanceStore(self.database_path).initialize()

    def test_failed_remittance_insert_rolls_back_counter_and_request(self):
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                """
                CREATE TRIGGER reject_remittance BEFORE INSERT ON remittances
                BEGIN SELECT RAISE(ABORT, 'write unavailable'); END
                """
            )
        with self.assertLogs("remittance_store", level="ERROR"):
            with self.assertRaises(sqlite3.IntegrityError):
                self.store.create_remittance(
                    self.make_request(),
                    initial_status=PENDING,
                    commission_rate=0.04,
                )
        self.assertEqual(self.store.load_remittances(), [])

        with sqlite3.connect(self.database_path) as connection:
            connection.execute("DROP TRIGGER reject_remittance")
        saved = self.store.create_remittance(
            self.make_request(),
            initial_status=PENDING,
            commission_rate=0.04,
        )
        self.assertTrue(saved["tracking_code"].endswith("-1001"))

    def test_database_is_created_with_private_permissions(self):
        self.assertEqual(Path(self.database_path).stat().st_mode & 0o777, 0o600)

    def test_database_path_can_be_configured_from_environment(self):
        with patch.dict(os.environ, {
            "BOT_TOKEN": "synthetic-test",
            "DATABASE_PATH": self.database_path,
        }):
            self.assertEqual(load_settings().database_path, self.database_path)

    def test_memory_database_keeps_schema_between_operations(self):
        store = RemittanceStore(":memory:")
        try:
            store.initialize()
            saved = store.create_remittance(
                self.make_request(),
                initial_status=PENDING,
                commission_rate=0.04,
            )
            self.assertEqual(
                store.get_remittance(saved["tracking_code"])["status"],
                PENDING,
            )
        finally:
            store.close()

    def test_non_finite_or_non_positive_amount_is_not_stored(self):
        for amount in (0, -1, float("nan"), float("inf"), float("-inf")):
            with self.subTest(amount=amount):
                request = self.make_request()
                request["amount"] = amount
                with self.assertRaises(ValueError):
                    self.store.create_remittance(
                        request,
                        initial_status=PENDING,
                        commission_rate=0.04,
                    )
                self.assertEqual(self.store.load_remittances(), [])


if __name__ == "__main__":
    unittest.main()
