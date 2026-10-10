import json
import logging
import math
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


LOGGER = logging.getLogger(__name__)
DEFAULT_DATABASE_PATH = "data/sarwari_exchange.sqlite3"


class RemittanceStore:
    def __init__(self, database_path: str = DEFAULT_DATABASE_PATH):
        self._is_memory = database_path == ":memory:"
        self.database_path = (
            f"file:sarwari-{uuid.uuid4().hex}?mode=memory&cache=shared"
            if self._is_memory
            else str(Path(database_path).expanduser())
        )
        self._keeper = (
            sqlite3.connect(self.database_path, uri=True, check_same_thread=False)
            if self._is_memory
            else None
        )

    @contextmanager
    def _connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            if not self._is_memory:
                Path(self.database_path).resolve().parent.mkdir(
                    parents=True, exist_ok=True, mode=0o700
                )
            connection = sqlite3.connect(
                self.database_path,
                timeout=30,
                uri=self._is_memory,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            if write:
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except sqlite3.Error:
            if connection:
                connection.rollback()
            LOGGER.exception("SQLite remittance storage failed")
            raise
        except OSError as error:
            LOGGER.exception("Could not access SQLite remittance storage")
            raise sqlite3.OperationalError(
                f"Cannot access remittance database at {self.database_path}"
            ) from error
        except Exception:
            if connection:
                connection.rollback()
            raise
        finally:
            if connection:
                connection.close()

    def initialize(self) -> None:
        with self._connection(write=True) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS remittances (
                    tracking_code TEXT PRIMARY KEY,
                    customer_chat_id INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    amount REAL NOT NULL,
                    currency TEXT NOT NULL,
                    commission REAL NOT NULL,
                    total REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    status_updated_at TEXT NOT NULL,
                    request_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_remittances_customer
                ON remittances(customer_chat_id, created_at)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS customers (
                    customer_chat_id INTEGER PRIMARY KEY,
                    customer_code TEXT NOT NULL UNIQUE
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS remittance_drafts (
                    customer_chat_id INTEGER PRIMARY KEY,
                    updated_at TEXT NOT NULL,
                    request_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS counters (
                    name TEXT PRIMARY KEY,
                    value INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS remittance_status_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tracking_code TEXT NOT NULL
                        REFERENCES remittances(tracking_code) ON DELETE CASCADE,
                    status TEXT NOT NULL,
                    changed_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS automatic_rates (
                    rate_key TEXT PRIMARY KEY,
                    item_kind TEXT NOT NULL,
                    base_currency TEXT NOT NULL,
                    quote_currency TEXT NOT NULL,
                    rate_type TEXT NOT NULL,
                    value REAL NOT NULL CHECK(value > 0),
                    unit TEXT NOT NULL,
                    purity TEXT,
                    source TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    source_updated_at TEXT,
                    retrieved_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    base_unit_scale REAL NOT NULL DEFAULT 1.0
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS manual_rates (
                    rate_key TEXT PRIMARY KEY,
                    item_kind TEXT NOT NULL,
                    base_currency TEXT NOT NULL,
                    quote_currency TEXT NOT NULL,
                    rate_type TEXT NOT NULL,
                    value REAL NOT NULL CHECK(value > 0),
                    unit TEXT NOT NULL,
                    purity TEXT,
                    source TEXT NOT NULL,
                    updated_by INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    base_unit_scale REAL NOT NULL DEFAULT 1.0
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS rate_adjustments (
                    rate_key TEXT PRIMARY KEY,
                    adjustment REAL NOT NULL,
                    updated_by INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS rate_sources (
                    source_id TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL CHECK(enabled IN (0, 1)),
                    updated_by INTEGER,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS rate_audit_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rate_key TEXT NOT NULL,
                    action TEXT NOT NULL,
                    old_value_json TEXT,
                    new_value_json TEXT,
                    administrator_id INTEGER NOT NULL,
                    changed_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS rate_source_health (
                    source_id TEXT PRIMARY KEY,
                    last_attempt_at TEXT,
                    last_success_at TEXT,
                    last_error TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS bot_settings (
                    setting_key TEXT PRIMARY KEY,
                    setting_value TEXT NOT NULL,
                    updated_by INTEGER,
                    updated_at TEXT NOT NULL
                )
                """
            )
            for table in ("automatic_rates", "manual_rates"):
                columns = {
                    row["name"]
                    for row in connection.execute(f"PRAGMA table_info({table})")
                }
                if "base_unit_scale" not in columns:
                    connection.execute(
                        f"ALTER TABLE {table} ADD COLUMN "
                        "base_unit_scale REAL NOT NULL DEFAULT 1.0"
                    )
            connection.executemany(
                "INSERT OR IGNORE INTO counters(name, value) VALUES (?, ?)",
                (("remittance", 1000), ("customer", 10024)),
            )
            connection.executemany(
                """
                INSERT OR IGNORE INTO rate_sources(source_id, enabled, updated_at)
                VALUES (?, ?, ?)
                """,
                (
                    ("frankfurter", 1, self._now()),
                    ("frankfurter_dab", 1, self._now()),
                    ("sarafi_af", 1, self._now()),
                ),
            )
        if not self._is_memory:
            try:
                os.chmod(self.database_path, 0o600)
            except OSError as error:
                LOGGER.exception("Could not secure SQLite remittance database permissions")
                raise sqlite3.OperationalError(
                    f"Cannot secure remittance database at {self.database_path}"
                ) from error

    def close(self) -> None:
        if self._keeper:
            self._keeper.close()
            self._keeper = None

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _decode_request(row: sqlite3.Row) -> dict:
        request = json.loads(row["request_json"])
        request.update({
            "tracking_code": row["tracking_code"],
            "customer_chat_id": row["customer_chat_id"],
            "status": row["status"],
            "amount": row["amount"],
            "currency": row["currency"],
            "commission": row["commission"],
            "total": row["total"],
            "created_at": row["created_at"],
            "status_updated_at": row["status_updated_at"],
        })
        return request

    def load_remittances(self) -> list[dict]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM remittances ORDER BY created_at, tracking_code"
            ).fetchall()
        return [self._decode_request(row) for row in rows]

    def get_remittance(self, tracking_code: str) -> dict | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM remittances WHERE tracking_code = ?",
                (tracking_code,),
            ).fetchone()
        return self._decode_request(row) if row else None

    def get_customer_remittances(self, customer_chat_id: int) -> list[dict]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM remittances
                WHERE customer_chat_id = ?
                ORDER BY created_at, tracking_code
                """,
                (customer_chat_id,),
            ).fetchall()
        return [self._decode_request(row) for row in rows]

    def get_draft(self, customer_chat_id: int) -> dict | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT request_json FROM remittance_drafts
                WHERE customer_chat_id = ?
                """,
                (customer_chat_id,),
            ).fetchone()
        return json.loads(row["request_json"]) if row else None

    def save_draft(self, customer_chat_id: int, request: dict) -> None:
        with self._connection(write=True) as connection:
            connection.execute(
                """
                INSERT INTO remittance_drafts(customer_chat_id, updated_at, request_json)
                VALUES (?, ?, ?)
                ON CONFLICT(customer_chat_id) DO UPDATE SET
                    updated_at = excluded.updated_at,
                    request_json = excluded.request_json
                """,
                (
                    customer_chat_id,
                    self._now(),
                    json.dumps(request, ensure_ascii=False, allow_nan=False),
                ),
            )

    def delete_draft(self, customer_chat_id: int) -> None:
        with self._connection(write=True) as connection:
            connection.execute(
                "DELETE FROM remittance_drafts WHERE customer_chat_id = ?",
                (customer_chat_id,),
            )

    def get_or_create_customer_code(self, customer_chat_id: int) -> str:
        with self._connection(write=True) as connection:
            row = connection.execute(
                "SELECT customer_code FROM customers WHERE customer_chat_id = ?",
                (customer_chat_id,),
            ).fetchone()
            if row:
                return str(row["customer_code"])
            connection.execute(
                "UPDATE counters SET value = value + 1 WHERE name = 'customer'"
            )
            counter = connection.execute(
                "SELECT value FROM counters WHERE name = 'customer'"
            ).fetchone()
            customer_code = f"CUS-{counter['value']}"
            connection.execute(
                "INSERT INTO customers(customer_chat_id, customer_code) VALUES (?, ?)",
                (customer_chat_id, customer_code),
            )
            return customer_code

    def create_remittance(
        self,
        request: dict,
        *,
        initial_status: str,
        commission_rate: float,
    ) -> dict:
        amount = float(request["amount"])
        if not math.isfinite(amount) or amount <= 0:
            raise ValueError("Remittance amount must be finite and positive")
        created_at = self._now()
        date_part = datetime.now().strftime("%Y%m%d")
        with self._connection(write=True) as connection:
            connection.execute(
                "UPDATE counters SET value = value + 1 WHERE name = 'remittance'"
            )
            counter = connection.execute(
                "SELECT value FROM counters WHERE name = 'remittance'"
            ).fetchone()
            tracking_code = f"LA-{date_part}-{counter['value']}"
            commission = round(amount * commission_rate, 2)
            total = round(amount + commission, 2)
            persisted = dict(request)
            persisted.update({
                "tracking_code": tracking_code,
                "customer_chat_id": int(request["customer_chat_id"]),
                "status": initial_status,
                "amount": amount,
                "commission": commission,
                "total": total,
                "created_at": created_at,
                "status_updated_at": created_at,
            })
            connection.execute(
                """
                INSERT INTO remittances(
                    tracking_code, customer_chat_id, status, amount, currency,
                    commission, total, created_at, status_updated_at, request_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tracking_code,
                    persisted["customer_chat_id"],
                    initial_status,
                    amount,
                    str(request["currency"]),
                    commission,
                    total,
                    created_at,
                    created_at,
                    json.dumps(persisted, ensure_ascii=False, allow_nan=False),
                ),
            )
            connection.execute(
                """
                INSERT INTO remittance_status_history(tracking_code, status, changed_at)
                VALUES (?, ?, ?)
                """,
                (tracking_code, initial_status, created_at),
            )
            connection.execute(
                "DELETE FROM remittance_drafts WHERE customer_chat_id = ?",
                (persisted["customer_chat_id"],),
            )
        return persisted

    def transition_status(
        self,
        tracking_code: str,
        *,
        expected_statuses: set[str],
        new_status: str,
    ) -> dict | None:
        if not expected_statuses:
            return None
        with self._connection(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM remittances WHERE tracking_code = ?",
                (tracking_code,),
            ).fetchone()
            if not row or row["status"] not in expected_statuses:
                return None
            updated_at = self._now()
            connection.execute(
                """
                UPDATE remittances
                SET status = ?, status_updated_at = ?
                WHERE tracking_code = ?
                """,
                (new_status, updated_at, tracking_code),
            )
            request = self._decode_request(row)
            request["status"] = new_status
            request["status_updated_at"] = updated_at
            request_json = json.loads(row["request_json"])
            request_json.update({
                "status": new_status,
                "status_updated_at": updated_at,
            })
            connection.execute(
                "UPDATE remittances SET request_json = ? WHERE tracking_code = ?",
                (json.dumps(request_json, ensure_ascii=False, allow_nan=False), tracking_code),
            )
            connection.execute(
                """
                INSERT INTO remittance_status_history(tracking_code, status, changed_at)
                VALUES (?, ?, ?)
                """,
                (tracking_code, new_status, updated_at),
            )
            return request

    def get_status_history(self, tracking_code: str) -> list[dict[str, str]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT status, changed_at FROM remittance_status_history
                WHERE tracking_code = ? ORDER BY id
                """,
                (tracking_code,),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def fx_rate_key(base_currency: str, quote_currency: str, rate_type: str) -> str:
        return f"fx:{base_currency.upper()}:{quote_currency.upper()}:{rate_type}"

    @staticmethod
    def gold_rate_key(unit: str, currency: str, purity: str | None = None) -> str:
        return f"gold:{unit.strip().lower()}:{currency.upper()}:{(purity or '').strip().lower()}"

    @staticmethod
    def _validate_rate(value: float) -> float:
        rate = float(value)
        if not math.isfinite(rate) or rate <= 0:
            raise ValueError("Rate must be finite and positive")
        return rate

    @staticmethod
    def _rate_dict(row: sqlite3.Row, *, is_manual: bool) -> dict:
        rate = dict(row)
        rate["is_manual"] = is_manual
        return rate

    def list_effective_rates(self) -> list[dict]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT a.*, 0 AS is_manual
                FROM automatic_rates AS a
                WHERE NOT EXISTS (
                    SELECT 1 FROM manual_rates AS m WHERE m.rate_key = a.rate_key
                )
                UNION ALL
                SELECT m.rate_key, m.item_kind, m.base_currency, m.quote_currency,
                       m.rate_type, m.value, m.unit, m.purity, m.source,
                       NULL AS source_id, NULL AS source_updated_at,
                       m.updated_at AS retrieved_at, m.updated_at,
                       m.base_unit_scale, 1 AS is_manual
                FROM manual_rates AS m
                ORDER BY rate_key
                """
            ).fetchall()
            adjustments = {
                row["rate_key"]: dict(row)
                for row in connection.execute(
                    "SELECT * FROM rate_adjustments"
                ).fetchall()
            }
        rates = []
        for row in rows:
            rate = self._rate_dict(row, is_manual=bool(row["is_manual"]))
            adjustment = adjustments.get(rate["rate_key"])
            if adjustment and not rate["is_manual"]:
                rate["automatic_value"] = rate["value"]
                rate["adjustment"] = adjustment["adjustment"]
                rate["value"] = self._validate_rate(
                    rate["value"] + adjustment["adjustment"]
                )
                rate["is_adjusted"] = True
                rate["adjustment_updated_by"] = adjustment["updated_by"]
                rate["adjustment_updated_at"] = adjustment["updated_at"]
            else:
                rate["automatic_value"] = rate["value"] if not rate["is_manual"] else None
                rate["adjustment"] = None
                rate["is_adjusted"] = False
            rate["mode"] = (
                "manual" if rate["is_manual"]
                else "automatic_adjusted" if rate["is_adjusted"]
                else "automatic"
            )
            rates.append(rate)
        return rates

    def get_rate(self, rate_key: str) -> dict | None:
        return next(
            (rate for rate in self.list_effective_rates() if rate["rate_key"] == rate_key),
            None,
        )

    def get_automatic_rates(self, source_id: str) -> list[dict]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM automatic_rates WHERE source_id = ? ORDER BY rate_key",
                (source_id,),
            ).fetchall()
        return [self._rate_dict(row, is_manual=False) for row in rows]

    def upsert_automatic_rates(self, source_id: str, rates: list[dict]) -> None:
        now = self._now()
        prepared = []
        for rate in rates:
            value = self._validate_rate(rate["value"])
            prepared.append((
                rate["rate_key"],
                rate["item_kind"],
                rate["base_currency"],
                rate["quote_currency"],
                rate["rate_type"],
                value,
                rate["unit"],
                rate.get("purity"),
                rate["source"],
                source_id,
                rate.get("source_updated_at"),
                rate["retrieved_at"],
                now,
                self._validate_rate(rate.get("base_unit_scale", 1.0)),
            ))
        with self._connection(write=True) as connection:
            connection.executemany(
                """
                INSERT INTO automatic_rates(
                    rate_key, item_kind, base_currency, quote_currency, rate_type,
                    value, unit, purity, source, source_id, source_updated_at,
                    retrieved_at, updated_at, base_unit_scale
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(rate_key) DO UPDATE SET
                    item_kind = excluded.item_kind,
                    base_currency = excluded.base_currency,
                    quote_currency = excluded.quote_currency,
                    rate_type = excluded.rate_type,
                    value = excluded.value,
                    unit = excluded.unit,
                    purity = excluded.purity,
                    source = excluded.source,
                    source_id = excluded.source_id,
                    source_updated_at = excluded.source_updated_at,
                    retrieved_at = excluded.retrieved_at,
                    updated_at = excluded.updated_at,
                    base_unit_scale = excluded.base_unit_scale
                """,
                prepared,
            )

    def set_manual_rate(
        self,
        *,
        rate_key: str,
        item_kind: str,
        base_currency: str,
        quote_currency: str,
        rate_type: str,
        value: float,
        unit: str,
        purity: str | None,
        administrator_id: int,
        base_unit_scale: float = 1.0,
        source: str = "مدیریت ربات",
    ) -> str:
        value = self._validate_rate(value)
        base_unit_scale = self._validate_rate(base_unit_scale)
        source = source.strip()
        if not source or len(source) > 120:
            raise ValueError("Rate source must contain 1 to 120 characters")
        now = self._now()
        rate = {
            "rate_key": rate_key,
            "item_kind": item_kind,
            "base_currency": base_currency,
            "quote_currency": quote_currency,
            "rate_type": rate_type,
            "value": value,
            "unit": unit,
            "purity": purity,
            "source": source,
            "updated_by": administrator_id,
            "updated_at": now,
            "base_unit_scale": base_unit_scale,
        }
        with self._connection(write=True) as connection:
            old = connection.execute(
                "SELECT * FROM manual_rates WHERE rate_key = ?", (rate_key,)
            ).fetchone()
            action = "manual_edit" if old else "manual_add"
            connection.execute(
                """
                INSERT INTO manual_rates(
                    rate_key, item_kind, base_currency, quote_currency,
                    rate_type, value, unit, purity, source, updated_by,
                    updated_at, base_unit_scale
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(rate_key) DO UPDATE SET
                    item_kind = excluded.item_kind,
                    base_currency = excluded.base_currency,
                    quote_currency = excluded.quote_currency,
                    rate_type = excluded.rate_type,
                    value = excluded.value,
                    unit = excluded.unit,
                    purity = excluded.purity,
                    source = excluded.source,
                    updated_by = excluded.updated_by,
                    updated_at = excluded.updated_at,
                    base_unit_scale = excluded.base_unit_scale
                """,
                (
                    rate_key, item_kind, base_currency, quote_currency, rate_type,
                    value, unit, purity, rate["source"], administrator_id, now,
                    base_unit_scale,
                ),
            )
            old_adjustment = connection.execute(
                "SELECT * FROM rate_adjustments WHERE rate_key = ?", (rate_key,)
            ).fetchone()
            if old_adjustment:
                connection.execute(
                    "DELETE FROM rate_adjustments WHERE rate_key = ?", (rate_key,)
                )
                connection.execute(
                    """
                    INSERT INTO rate_audit_history(
                        rate_key, action, old_value_json, new_value_json,
                        administrator_id, changed_at
                    ) VALUES (?, 'adjustment_removed', ?, NULL, ?, ?)
                    """,
                    (
                        rate_key, json.dumps(dict(old_adjustment), ensure_ascii=False),
                        administrator_id, now,
                    ),
                )
            connection.execute(
                """
                INSERT INTO rate_audit_history(
                    rate_key, action, old_value_json, new_value_json,
                    administrator_id, changed_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    rate_key,
                    action,
                    json.dumps(dict(old), ensure_ascii=False) if old else None,
                    json.dumps(rate, ensure_ascii=False),
                    administrator_id,
                    now,
                ),
            )
        return action

    def restore_automatic_rate(self, rate_key: str, administrator_id: int) -> bool:
        now = self._now()
        with self._connection(write=True) as connection:
            old = connection.execute(
                "SELECT * FROM manual_rates WHERE rate_key = ?", (rate_key,)
            ).fetchone()
            automatic = connection.execute(
                "SELECT * FROM automatic_rates WHERE rate_key = ?", (rate_key,)
            ).fetchone()
            adjustment = connection.execute(
                "SELECT * FROM rate_adjustments WHERE rate_key = ?", (rate_key,)
            ).fetchone()
            if not old and not adjustment:
                return False
            connection.execute("DELETE FROM manual_rates WHERE rate_key = ?", (rate_key,))
            connection.execute("DELETE FROM rate_adjustments WHERE rate_key = ?", (rate_key,))
            connection.execute(
                """
                INSERT INTO rate_audit_history(
                    rate_key, action, old_value_json, new_value_json,
                    administrator_id, changed_at
                ) VALUES (?, 'restore_automatic', ?, ?, ?, ?)
                """,
                (
                    rate_key,
                    json.dumps(
                        {
                            "manual": dict(old) if old else None,
                            "adjustment": dict(adjustment) if adjustment else None,
                        },
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        {
                            "automatic": dict(automatic) if automatic else None,
                            "adjustment": dict(adjustment) if adjustment else None,
                        },
                        ensure_ascii=False,
                    ),
                    administrator_id,
                    now,
                ),
            )
            return True

    def set_rate_adjustment(
        self, rate_key: str, adjustment: float, administrator_id: int
    ) -> None:
        if not math.isfinite(float(adjustment)):
            raise ValueError("Adjustment must be finite")
        now = self._now()
        with self._connection(write=True) as connection:
            automatic = connection.execute(
                "SELECT * FROM automatic_rates WHERE rate_key = ?", (rate_key,)
            ).fetchone()
            manual = connection.execute(
                "SELECT 1 FROM manual_rates WHERE rate_key = ?", (rate_key,)
            ).fetchone()
            if not automatic:
                raise ValueError("Automatic rate is unavailable for adjustment")
            if manual:
                raise ValueError("Restore automatic rate before applying an adjustment")
            base_value = self._validate_rate(automatic["value"])
            new_value = self._validate_rate(base_value + float(adjustment))
            if abs(float(adjustment)) > base_value * 0.25:
                raise ValueError("Adjustment exceeds the allowed 25 percent")
            old = connection.execute(
                "SELECT * FROM rate_adjustments WHERE rate_key = ?", (rate_key,)
            ).fetchone()
            new = {
                "rate_key": rate_key,
                "adjustment": float(adjustment),
                "updated_by": administrator_id,
                "updated_at": now,
                "automatic_value": base_value,
                "effective_value": new_value,
            }
            connection.execute(
                """
                INSERT INTO rate_adjustments(rate_key, adjustment, updated_by, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(rate_key) DO UPDATE SET
                    adjustment = excluded.adjustment,
                    updated_by = excluded.updated_by,
                    updated_at = excluded.updated_at
                """,
                (rate_key, float(adjustment), administrator_id, now),
            )
            connection.execute(
                """
                INSERT INTO rate_audit_history(
                    rate_key, action, old_value_json, new_value_json,
                    administrator_id, changed_at
                ) VALUES (?, 'adjustment_set', ?, ?, ?, ?)
                """,
                (
                    rate_key, json.dumps(dict(old), ensure_ascii=False) if old else None,
                    json.dumps(new, ensure_ascii=False), administrator_id, now,
                ),
            )

    def record_source_health(
        self,
        source_id: str,
        *,
        success: bool,
        error: str | None = None,
    ) -> None:
        now = self._now()
        with self._connection(write=True) as connection:
            old = connection.execute(
                "SELECT * FROM rate_source_health WHERE source_id = ?",
                (source_id,),
            ).fetchone()
            last_success_at = old["last_success_at"] if old else None
            if success:
                last_success_at = now
                error = None
            connection.execute(
                """
                INSERT INTO rate_source_health(
                    source_id, last_attempt_at, last_success_at, last_error
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    last_attempt_at = excluded.last_attempt_at,
                    last_success_at = excluded.last_success_at,
                    last_error = excluded.last_error
                """,
                (source_id, now, last_success_at, error),
            )

    def get_source_health(self, source_id: str) -> dict | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM rate_source_health WHERE source_id = ?",
                (source_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_setting(self, setting_key: str, default: str | None = None) -> str | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT setting_value FROM bot_settings WHERE setting_key = ?",
                (setting_key,),
            ).fetchone()
        return row["setting_value"] if row else default

    def set_setting(
        self,
        setting_key: str,
        setting_value: str,
        administrator_id: int,
    ) -> None:
        now = self._now()
        with self._connection(write=True) as connection:
            old = connection.execute(
                "SELECT * FROM bot_settings WHERE setting_key = ?",
                (setting_key,),
            ).fetchone()
            connection.execute(
                """
                INSERT INTO bot_settings(
                    setting_key, setting_value, updated_by, updated_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(setting_key) DO UPDATE SET
                    setting_value = excluded.setting_value,
                    updated_by = excluded.updated_by,
                    updated_at = excluded.updated_at
                """,
                (setting_key, setting_value, administrator_id, now),
            )
            connection.execute(
                """
                INSERT INTO rate_audit_history(
                    rate_key, action, old_value_json, new_value_json,
                    administrator_id, changed_at
                ) VALUES (?, 'setting_change', ?, ?, ?, ?)
                """,
                (
                    f"setting:{setting_key}",
                    json.dumps(dict(old), ensure_ascii=False) if old else None,
                    json.dumps(
                        {
                            "setting_value": setting_value,
                            "updated_by": administrator_id,
                            "updated_at": now,
                        },
                        ensure_ascii=False,
                    ),
                    administrator_id,
                    now,
                ),
            )

    def set_source_enabled(self, source_id: str, enabled: bool, administrator_id: int) -> None:
        now = self._now()
        with self._connection(write=True) as connection:
            old = connection.execute(
                "SELECT * FROM rate_sources WHERE source_id = ?", (source_id,)
            ).fetchone()
            if not old:
                raise ValueError("Unsupported automatic rate source")
            connection.execute(
                """
                UPDATE rate_sources SET enabled = ?, updated_by = ?, updated_at = ?
                WHERE source_id = ?
                """,
                (int(enabled), administrator_id, now, source_id),
            )
            connection.execute(
                """
                INSERT INTO rate_audit_history(
                    rate_key, action, old_value_json, new_value_json,
                    administrator_id, changed_at
                ) VALUES (?, 'source_toggle', ?, ?, ?, ?)
                """,
                (
                    f"source:{source_id}",
                    json.dumps(dict(old), ensure_ascii=False),
                    json.dumps({"enabled": enabled}, ensure_ascii=False),
                    administrator_id,
                    now,
                ),
            )

    def is_source_enabled(self, source_id: str) -> bool:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT enabled FROM rate_sources WHERE source_id = ?", (source_id,)
            ).fetchone()
        return bool(row["enabled"]) if row else False

    def list_sources(self) -> list[dict]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM rate_sources ORDER BY source_id"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_rate_audit_history(self, limit: int = 100) -> list[dict]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM rate_audit_history
                ORDER BY id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]
