import asyncio
from dataclasses import replace
from datetime import datetime, timezone

from config import Settings
from models import GoldPrice, ProviderResult, Rate
from services.dab import DABProvider
from services.frankfurter import FrankfurterProvider
from services.gold import GoldProvider
from services.sarai_shahzada import SaraiShahzadaProvider
from services.xe import XEProvider
from utils.cache import TTLCache


class RatesService:
    def __init__(self, settings: Settings):
        self.cache = TTLCache[ProviderResult](settings.cache_ttl_seconds)
        self.reference_cache = TTLCache[ProviderResult](settings.reference_cache_ttl_seconds)
        self.exchange_reference_cache = TTLCache[ProviderResult](
            settings.reference_cache_ttl_seconds
        )
        self.gold_provider = GoldProvider(settings.request_timeout)
        self.frankfurter_provider = FrankfurterProvider(settings.request_timeout)
        self.sarai_shahzada_provider = SaraiShahzadaProvider(settings)
        self.xe_provider = XEProvider(settings.xe_api_key, settings.xe_account_id, settings.request_timeout)
        self.dab_provider = DABProvider()

    async def _get(self, key: str, fetcher, force: bool = False) -> ProviderResult:
        cached = self.cache.get(key)
        if not force and self.cache.is_fresh(cached):
            return cached.value
        async with self.cache.lock_for(key):
            cached = self.cache.get(key)
            if not force and self.cache.is_fresh(cached):
                return cached.value
            result = await asyncio.to_thread(fetcher)
            if result.available:
                self.cache.set(key, result)
                return result
            if cached is not None:
                return self._stale(cached.value, result.message)
            return result

    @staticmethod
    def _stale(result: ProviderResult, message: str | None) -> ProviderResult:
        rates = tuple(replace(rate, is_stale=True) for rate in result.rates)
        gold = replace(result.gold, is_stale=True) if result.gold else None
        return ProviderResult(rates=rates, gold=gold, available=False, message=message)

    async def get_gold(self) -> ProviderResult:
        return await self._get("gold", self.gold_provider.fetch)

    async def refresh_gold(self) -> ProviderResult:
        return await self._get("gold", self.gold_provider.fetch, force=True)

    async def get_xe(self) -> ProviderResult:
        return await self._get("xe", self.xe_provider.fetch)

    async def get_reference_rates(self) -> ProviderResult:
        return await self._get_reference()

    async def get_sarai_rates(self) -> ProviderResult:
        return await self._get("sarai-af", self.sarai_shahzada_provider.fetch)

    async def refresh_sarai_rates(self) -> ProviderResult:
        return await self._get("sarai-af", self.sarai_shahzada_provider.fetch, force=True)

    async def refresh_reference_rates(self) -> ProviderResult:
        return await self._get_reference(force=True)

    async def get_dab(self) -> ProviderResult:
        return await self._get("dab", self.dab_provider.fetch)

    async def refresh(self) -> dict[str, ProviderResult]:
        gold, reference, xe, dab = await asyncio.gather(
            self.refresh_gold(),
            self.refresh_reference_rates(),
            self._get("xe", self.xe_provider.fetch, force=True),
            self._get("dab", self.dab_provider.fetch, force=True),
        )
        return {"gold": gold, "reference": reference, "xe": xe, "dab": dab}

    async def _get_reference(self, force: bool = False) -> ProviderResult:
        key = "exchange-rate-api"
        cached = self.exchange_reference_cache.get(key)
        if not force and self.exchange_reference_cache.is_fresh(cached):
            return cached.value
        async with self.exchange_reference_cache.lock_for(key):
            cached = self.exchange_reference_cache.get(key)
            if not force and self.exchange_reference_cache.is_fresh(cached):
                return cached.value
            result = await asyncio.to_thread(self.frankfurter_provider.fetch)
            if result.available:
                self.exchange_reference_cache.set(key, result)
                return result
            if cached is not None:
                return self._stale(cached.value, result.message)
            return result
