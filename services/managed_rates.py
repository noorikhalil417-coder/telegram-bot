import asyncio
import logging
from datetime import datetime, timedelta, timezone

from remittance_store import RemittanceStore
from services.rates import RatesService

LOGGER = logging.getLogger(__name__)
REFERENCE_SOURCE_ID = "frankfurter"
DAB_REFERENCE_SOURCE_ID = "frankfurter_dab"
SARAI_SOURCE_ID = "sarafi_af"
REFERENCE_REFRESH_INTERVAL = timedelta(hours=6)
SARAI_REFRESH_INTERVAL = timedelta(minutes=15)
SARAI_FAILURE_RETRY_INTERVAL = timedelta(minutes=1)


class ManagedRatesService:
    def __init__(self, store: RemittanceStore, rates_service: RatesService):
        self.store = store
        self.rates_service = rates_service
        self._locks = {
            REFERENCE_SOURCE_ID: asyncio.Lock(),
            DAB_REFERENCE_SOURCE_ID: asyncio.Lock(),
            SARAI_SOURCE_ID: asyncio.Lock(),
        }

    @staticmethod
    def _is_due(rates: list[dict], interval: timedelta) -> bool:
        if not rates:
            return True
        retrieved_values = [
            rate.get("retrieved_at")
            for rate in rates
            if rate.get("retrieved_at")
        ]
        if not retrieved_values:
            return True
        latest = max(datetime.fromisoformat(value) for value in retrieved_values)
        if latest.tzinfo is None:
            latest = latest.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - latest >= interval

    async def refresh_reference(self, *, force: bool = False) -> bool:
        if not self.store.is_source_enabled(REFERENCE_SOURCE_ID):
            return False
        async with self._locks[REFERENCE_SOURCE_ID]:
            cached = self.store.get_automatic_rates(REFERENCE_SOURCE_ID)
            if not force and not self._is_due(cached, REFERENCE_REFRESH_INTERVAL):
                return bool(cached)
            result = await self.rates_service.refresh_reference_rates()
            if not result.available or not result.rates:
                LOGGER.warning("Reference provider unavailable; stored rates are retained")
                return False
            retrieved_at = (
                result.rates[0].retrieved_at or result.rates[0].timestamp
            ).isoformat()
            self.store.upsert_automatic_rates(
                REFERENCE_SOURCE_ID,
                [
                    {
                        "rate_key": self.store.fx_rate_key(
                            rate.base_currency,
                            rate.quote_currency,
                            rate.rate_type,
                        ),
                        "item_kind": "currency",
                        "base_currency": rate.base_currency,
                        "quote_currency": rate.quote_currency,
                        "rate_type": rate.rate_type,
                        "value": rate.value,
                        "unit": rate.source_unit or rate.quote_currency,
                        "purity": None,
                        "source": rate.source,
                        "source_updated_at": (
                            rate.source_updated_at.isoformat()
                            if rate.source_updated_at
                            else None
                        ),
                        "retrieved_at": (
                            rate.retrieved_at or rate.timestamp
                        ).isoformat(),
                    }
                    for rate in result.rates
                ],
            )
            return True

    async def refresh_sarai(self, *, force: bool = False) -> bool:
        if not self.store.is_source_enabled(SARAI_SOURCE_ID):
            return False
        async with self._locks[SARAI_SOURCE_ID]:
            cached = self.store.get_automatic_rates(SARAI_SOURCE_ID)
            if not force and not self._is_due(cached, SARAI_REFRESH_INTERVAL):
                return bool(cached)
            health = self.store.get_source_health(SARAI_SOURCE_ID) or {}
            last_attempt = health.get("last_attempt_at")
            if not force and last_attempt:
                attempted_at = datetime.fromisoformat(last_attempt)
                if attempted_at.tzinfo is None:
                    attempted_at = attempted_at.replace(tzinfo=timezone.utc)
                if (
                    datetime.now(timezone.utc) - attempted_at
                    < SARAI_FAILURE_RETRY_INTERVAL
                ):
                    return False
            result = await self.rates_service.refresh_sarai_rates()
            if not result.available or not result.rates:
                error = result.message or "منبع نرخ دادهٔ معتبر برنگرداند."
                self.store.record_source_health(
                    SARAI_SOURCE_ID, success=False, error=error
                )
                LOGGER.warning("Sarafi.af rates unavailable; cached rates are retained")
                return False
            self.store.upsert_automatic_rates(
                SARAI_SOURCE_ID,
                [
                    {
                        "rate_key": self.store.fx_rate_key(
                            rate.base_currency,
                            rate.quote_currency,
                            rate.rate_type,
                        ),
                        "item_kind": "currency",
                        "base_currency": rate.base_currency,
                        "quote_currency": rate.quote_currency,
                        "rate_type": rate.rate_type,
                        "value": rate.value,
                        "unit": rate.source_unit or rate.quote_currency,
                        "base_unit_scale": rate.base_unit_scale,
                        "purity": None,
                        "source": rate.source,
                        "source_updated_at": (
                            rate.source_updated_at.isoformat()
                            if rate.source_updated_at
                            else None
                        ),
                        "retrieved_at": (
                            rate.retrieved_at or rate.timestamp
                        ).isoformat(),
                    }
                    for rate in result.rates
                ],
            )
            self.store.record_source_health(SARAI_SOURCE_ID, success=True)
            return True

    async def refresh_gold(self, *, force: bool = False) -> bool:
        return False

    async def refresh_dab_indicative(self, *, force: bool = False) -> bool:
        if not self.store.is_source_enabled(DAB_REFERENCE_SOURCE_ID):
            return False
        async with self._locks[DAB_REFERENCE_SOURCE_ID]:
            cached = self.store.get_automatic_rates(DAB_REFERENCE_SOURCE_ID)
            if not force and not self._is_due(cached, REFERENCE_REFRESH_INTERVAL):
                return bool(cached)
            result = await asyncio.to_thread(
                self.rates_service.frankfurter_provider.fetch_dab_indicative_afn
            )
            if not result.available or not result.rates:
                LOGGER.warning("Frankfurter DAB rate unavailable; stored rate is retained")
                return False
            rate = result.rates[0]
            self.store.upsert_automatic_rates(
                DAB_REFERENCE_SOURCE_ID,
                [{
                    "rate_key": self.store.fx_rate_key(
                        rate.base_currency, rate.quote_currency, rate.rate_type
                    ),
                    "item_kind": "currency",
                    "base_currency": rate.base_currency,
                    "quote_currency": rate.quote_currency,
                    "rate_type": rate.rate_type,
                    "value": rate.value,
                    "unit": rate.source_unit or rate.quote_currency,
                    "purity": None,
                    "source": rate.source,
                    "source_updated_at": (
                        rate.source_updated_at.isoformat()
                        if rate.source_updated_at
                        else None
                    ),
                    "retrieved_at": (rate.retrieved_at or rate.timestamp).isoformat(),
                }],
            )
            return True

    async def refresh_all(self, *, force: bool = False) -> None:
        await asyncio.gather(
            self.refresh_sarai(force=force),
        )
        await self.refresh_gold(force=force)

    async def get_effective_rates(self) -> list[dict]:
        await self.refresh_sarai()
        rates = self.store.list_effective_rates()
        automatic = self.store.get_automatic_rates(SARAI_SOURCE_ID)
        source_stale = (
            not self.store.is_source_enabled(SARAI_SOURCE_ID)
            or self._is_due(automatic, SARAI_REFRESH_INTERVAL)
        )
        for rate in rates:
            if rate.get("source_id") == SARAI_SOURCE_ID:
                rate["is_stale"] = source_stale
            elif rate.get("is_manual"):
                rate["is_stale"] = False
        return [
            rate for rate in rates
            if rate["item_kind"] == "currency"
            and (
                rate.get("source_id") == SARAI_SOURCE_ID
                or rate.get("is_manual")
            )
        ]

    async def get_effective_gold(self) -> list[dict]:
        await self.refresh_gold()
        rates = [
            rate
            for rate in self.store.list_effective_rates()
            if rate["item_kind"] == "gold"
        ]
        for rate in rates:
            rate["is_stale"] = False
        return rates
