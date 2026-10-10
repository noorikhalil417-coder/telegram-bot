from dataclasses import dataclass
from datetime import datetime
from typing import Literal


RateType = Literal[
    "cash_buy",
    "cash_sell",
    "transfer_buy",
    "transfer_sell",
    "mid_market",
    "indicative",
    "spot",
]
RateDirection = Literal["quote_per_base", "base_per_quote", "source_unspecified"]


@dataclass(frozen=True)
class Rate:
    """A provider rate observation without assuming a universal quote convention.

    ``base_currency`` and ``quote_currency`` are the adapter's currency
    identifiers; by themselves they do not prove the value's numerator or
    denominator. Only an explicit ``rate_direction`` establishes that relation.
    ``value`` is the provider's value and must not be inverted or rescaled unless
    the provider contract explicitly requires it. ``rate_type`` retains the
    provider's buy/sell or market label, not a customer-side interpretation.
    ``source_unit`` preserves the provider's unit label verbatim.
    ``timestamp`` is kept for compatibility; use ``retrieved_at`` and
    ``source_updated_at`` for the distinct retrieval and source-update times.
    """

    symbol: str
    base_currency: str
    quote_currency: str
    value: float
    source: str
    rate_type: RateType
    timestamp: datetime
    is_stale: bool = False
    next_update: datetime | None = None
    rate_direction: RateDirection = "source_unspecified"
    source_unit: str | None = None
    retrieved_at: datetime | None = None
    source_updated_at: datetime | None = None


@dataclass(frozen=True)
class GoldPrice:
    symbol: str
    ounce_usd: float
    gram_usd: float
    source: str
    timestamp: datetime
    is_stale: bool = False
    retrieved_at: datetime | None = None


@dataclass(frozen=True)
class ProviderResult:
    rates: tuple[Rate, ...] = ()
    gold: GoldPrice | None = None
    available: bool = True
    message: str | None = None
