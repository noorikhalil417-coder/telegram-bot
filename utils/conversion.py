import math


SUPPORTED_CUSTOMER_CURRENCIES = {"AFN", "USD", "EUR", "PKR", "IRR", "AED"}


def _usable_rate(rates: list[dict], currency: str, rate_type: str) -> dict:
    matches = [
        rate for rate in rates
        if rate["base_currency"] == currency
        and rate["quote_currency"] == "AFN"
        and rate["rate_type"] == rate_type
    ]
    if not matches:
        raise ValueError(f"نرخ معتبر {rate_type} برای {currency} موجود نیست.")
    rate = matches[0]
    if rate.get("is_stale"):
        raise ValueError(f"نرخ {currency} تازه نیست؛ محاسبه انجام نشد.")
    value = float(rate["value"])
    scale = float(rate.get("base_unit_scale", 1.0))
    if not math.isfinite(value) or value <= 0 or not math.isfinite(scale) or scale <= 0:
        raise ValueError(f"نرخ یا واحد {currency} معتبر نیست.")
    return rate


def convert_market_amount(
    rates: list[dict],
    amount: float,
    source_currency: str,
    target_currency: str,
) -> tuple[float, float, list[dict]]:
    amount = float(amount)
    source_currency = source_currency.upper()
    target_currency = target_currency.upper()
    if not math.isfinite(amount) or amount <= 0:
        raise ValueError("مبلغ باید یک عدد مثبت و معتبر باشد.")
    if (
        source_currency not in SUPPORTED_CUSTOMER_CURRENCIES
        or target_currency not in SUPPORTED_CUSTOMER_CURRENCIES
    ):
        raise ValueError("این ارز برای تبدیل بازار محلی پشتیبانی نمی‌شود.")
    if source_currency == target_currency:
        raise ValueError("ارز مبدأ و مقصد باید متفاوت باشند.")

    used_rates: list[dict] = []
    if source_currency == "AFN":
        sell = _usable_rate(rates, target_currency, "cash_sell")
        effective_rate = (
            float(sell["base_unit_scale"]) / float(sell["value"])
        )
        converted = amount * effective_rate
        used_rates.append(sell)
    elif target_currency == "AFN":
        buy = _usable_rate(rates, source_currency, "cash_buy")
        effective_rate = float(buy["value"]) / float(buy["base_unit_scale"])
        converted = amount * effective_rate
        used_rates.append(buy)
    else:
        buy = _usable_rate(rates, source_currency, "cash_buy")
        sell = _usable_rate(rates, target_currency, "cash_sell")
        source_afn_rate = float(buy["value"]) / float(buy["base_unit_scale"])
        target_afn_rate = float(sell["value"]) / float(sell["base_unit_scale"])
        effective_rate = source_afn_rate / target_afn_rate
        converted = amount * effective_rate
        used_rates.extend((buy, sell))

    if not math.isfinite(converted) or not math.isfinite(effective_rate):
        raise ValueError("نتیجه محاسبه معتبر نیست.")
    return converted, effective_rate, used_rates
