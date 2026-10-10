import asyncio
import logging
from datetime import datetime, timedelta, timezone

from remittance_store import RemittanceStore
from services.rates import RatesService

LOGGER = logging.getLogger(__name__)
REFERENCE_SOURCE_ID = "frankfurter"
DAB_REFERENCE_SOURCE_ID = "frankfurter_dab"
REFERENCE_REFRESH_INTERVAL = timedelta(hours=6)


class ManagedRatesService:
    def __init__(self, store: RemittanceStore, rates_service: RatesService):
        self.store = store
        self.rates_service = rates_service
        self._locks = {
            REFERENCE_SOURCE_ID: asyncio.Lock(),
            DAB_REFERENCE_SOURCE_ID: asyncio.Lock(),
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
            self.refresh_reference(force=force),
            self.refresh_dab_indicative(force=force),
        )
        await self.refresh_gold(force=force)

    async def get_effective_rates(self) -> list[dict]:
        await self.refresh_reference()
        rates = self.store.list_effective_rates()
        automatic = self.store.get_automatic_rates(REFERENCE_SOURCE_ID)
        source_stale = (
            not self.store.is_source_enabled(REFERENCE_SOURCE_ID)
            or self._is_due(automatic, REFERENCE_REFRESH_INTERVAL)
        )
        if source_stale:
            for rate in rates:
                if rate.get("source_id") == REFERENCE_SOURCE_ID:
                    rate["is_stale"] = True
        dab_rates = self.store.get_automatic_rates(DAB_REFERENCE_SOURCE_ID)
        dab_stale = (
            not self.store.is_source_enabled(DAB_REFERENCE_SOURCE_ID)
            or self._is_due(dab_rates, REFERENCE_REFRESH_INTERVAL)
        )
        for rate in rates:
            if rate.get("source_id") == DAB_REFERENCE_SOURCE_ID:
                source_date = rate.get("source_updated_at")
                if source_date:
                    source_time = datetime.fromisoformat(source_date)
                    if source_time.tzinfo is None:
                        source_time = source_time.replace(tzinfo=timezone.utc)
                    dab_stale = dab_stale or (
                        datetime.now(timezone.utc) - source_time > timedelta(days=2)
                    )
                rate["is_stale"] = dab_stale
        return [rate for rate in rates if rate["item_kind"] == "currency"]

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
