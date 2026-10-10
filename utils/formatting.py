from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from models import GoldPrice, Rate


def status_label(is_stale: bool) -> str:
    return "⚠️ آخرین داده معتبر" if is_stale else "🕐 آخرین بروزرسانی"


def format_local_timestamp(value) -> str:
    if value is None:
        return "اعلام نشده"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(ZoneInfo("Asia/Kabul")).strftime("%Y-%m-%d %H:%M")


def _display_rate_value(value: float) -> str:
    return str(int(value)) if value.is_integer() else str(value)


def format_gold(price: GoldPrice) -> str:
    return (
        "🥇 قیمت جهانی طلا\n"
        "━━━━━━━━━━━━━\n"
        f"💰 هر اونس: ${price.ounce_usd:,.2f}\n"
        f"⚖️ هر گرم: ${price.gram_usd:,.2f}\n"
        "━━━━━━━━━━━━━\n"
        f"{status_label(price.is_stale)}: {price.timestamp.isoformat()}\n"
        f"🕐 زمان دریافت: {price.retrieved_at.isoformat() if price.retrieved_at else 'نامشخص'}\n"
        f"📌 منبع: {price.source}\n\n"
        "⚠️ این Gold Spot جهانی است و الزاماً قیمت بازار طلای افغانستان یا اجرت ساخت نیست."
    )


def format_xe_rates(rates: tuple[Rate, ...]) -> str:
    if not rates:
        return "⚠️ نرخ میانی XE فعلاً در دسترس نیست."
    lines = ["💱 نرخ میانی ارز (XE)", "━━━━━━━━━━━━━"]
    if any(rate.is_stale for rate in rates):
        lines.append("⚠️ دادهٔ نمایش‌داده‌شده از cache قدیمی است و تازه نیست.")
    for rate in rates:
        value = _display_rate_value(rate.value)
        if rate.rate_direction == "quote_per_base":
            lines.append(f"1 {rate.base_currency} = {value} {rate.quote_currency}")
        elif rate.rate_direction == "base_per_quote":
            lines.append(f"1 {rate.quote_currency} = {value} {rate.base_currency}")
        else:
            lines.append(f"{rate.source} [{rate.rate_type}]: {value}")
    retrieved_at = max(
        (rate.retrieved_at or rate.timestamp for rate in rates),
        default=None,
    )
    source_updates = [rate.source_updated_at for rate in rates if rate.source_updated_at]
    lines.extend([
        "━━━━━━━━━━━━━",
        f"🕐 زمان دریافت: {format_local_timestamp(retrieved_at)}",
        f"🕐 بروزرسانی منبع: {format_local_timestamp(max(source_updates, default=None))}",
        "📌 منبع: XE (mid-market)",
        "⚠️ این نرخ خرید یا فروش صرافی افغانستان نیست.",
    ])
    return "\n".join(lines)


def format_reference_rates(rates: tuple[Rate, ...]) -> str:
    if not rates:
        return "⚠️ نرخ‌های خرید و فروش اسعار فعلاً در دسترس نیستند. لطفاً کمی بعد دوباره تلاش کنید."

    flags = {
        "USD": "💵", "EUR": "💶", "IRR": "🇮🇷", "PKR": "🇵🇰", "JPY": "🇯🇵", "GBP": "🇬🇧",
    }
    names = {
        "USD": "دالر آمریکا", "EUR": "یورو", "IRR": "تومان ایران (واحد منبع: هزار تومان)", "PKR": "روپیه پاکستان",
        "JPY": "ین جاپان", "GBP": "پوند انگلیس", "SAR": "ریال سعودی", "AED": "درهم امارات",
        "CHF": "فرانک سویس", "AUD": "دالر آسترالیا", "CAD": "دالر کانادا", "RUB": "روبل روسیه",
        "DKK": "کرون دنمارک", "SEK": "کرون سویدن", "NOK": "کرون ناروی", "TRY": "لیره ترکیه",
        "CNY": "ین چین", "KWD": "دینار کویت", "QAR": "ریال قطر", "BHD": "دینار بحرین",
    }
    grouped: dict[str, dict[str, Rate]] = {}
    for rate in rates:
        grouped.setdefault(rate.quote_currency, {})[rate.rate_type] = rate

    lines = [
        "📊 نرخ اسعار — SARWARI EXCHANGE",
        "مقادیر خام طبق ستون‌های منبع؛ بدون تبدیل واحد یا جهت‌دهی",
        "━━━━━━━━━━━━━━━━━━",
    ]

    for symbol in ("USD", "EUR", "IRR", "PKR", "JPY", "GBP"):
        values = grouped.get(symbol, {})
        buy = values.get("cash_buy")
        sell = values.get("cash_sell")
        if not buy or not sell:
            continue
        label = names.get(symbol, symbol)
        flag = flags.get(symbol, "💱")
        lines.extend([
            f"\n{flag} {label}",
            f"خرید (طبق منبع): {_display_rate_value(buy.value)}",
            f"فروش (طبق منبع): {_display_rate_value(sell.value)}",
        ])

    source = rates[0].source.rsplit(" (", 1)[0] if rates else "نامشخص"
    received_at = max(
        (rate.retrieved_at or rate.timestamp for rate in rates),
        default=None,
    )
    source_updates = [rate.source_updated_at for rate in rates if rate.source_updated_at]
    source_updated_at = max(source_updates, default=None)
    stale_note = "⚠️ دادهٔ نمایش‌داده‌شده از cache قدیمی است و تازه نیست.\n" if any(
        rate.is_stale for rate in rates
    ) else ""
    direction_note = "⚠️ جهت ریاضی نرخ در منبع مشخص نشده؛ خرید/فروش فقط عنوان ستون منبع است.\n" if any(
        rate.rate_direction == "source_unspecified" for rate in rates
    ) else ""
    lines.extend([
        "━━━━━━━━━━━━━━━━━━",
        f"{stale_note}{direction_note}🕐 زمان دریافت: {format_local_timestamp(received_at)}",
        f"🕐 بروزرسانی منبع: {format_local_timestamp(source_updated_at)}",
        f"📌 منبع: {source}",
    ])
    return "\n".join(lines)


def format_managed_rates(rates: list[dict]) -> str:
    if not rates:
        return "⚠️ نرخ خودکار یا دستی برای نمایش موجود نیست."
    type_labels = {
        "cash_buy": "خرید نقدی",
        "cash_sell": "فروش نقدی",
        "transfer_buy": "خرید حواله",
        "transfer_sell": "فروش حواله",
        "mid_market": "نرخ مرجع روزانه",
        "indicative": "نرخ Indicative منبع DAB",
        "spot": "نرخ لحظه‌ای ارائه‌دهنده",
        "local_market_manual": "قیمت دستی بازار محلی",
        "international_spot_manual": "قیمت دستی Spot جهانی",
    }
    lines = ["📊 نرخ اسعار", "━━━━━━━━━━━━━━━━━━"]
    for rate in rates:
        stale = " ⚠️ دادهٔ ذخیره‌شده و قدیمی" if rate.get("is_stale") else ""
        managed = (
            "دستی (مدیریت)"
            if rate["is_manual"]
            else "خودکار با اصلاح ثبت‌شده"
            if rate.get("is_adjusted")
            else "خودکار"
        )
        if rate["rate_type"] == "source_unspecified":
            quote = f"مقدار خام منبع: {_display_rate_value(rate['value'])} ({rate['unit']})"
        else:
            base_quantity = _display_rate_value(
                float(rate.get("base_unit_scale", 1.0))
            )
            quote = (
                f"{base_quantity} {rate['base_currency']} = "
                f"{_display_rate_value(rate['value'])} {rate['quote_currency']} "
                f"({rate['unit']})"
            )
        adjustment_note = ""
        if rate.get("is_adjusted"):
            adjustment_note = (
                f"\nنرخ خودکار: {_display_rate_value(rate['automatic_value'])}؛ "
                f"اصلاح مدیر: {rate['adjustment']:+g}"
            )
        lines.extend([
            f"\n{rate['base_currency']} → {rate['quote_currency']} "
            f"({type_labels.get(rate['rate_type'], rate['rate_type'])})",
            quote,
        ])
        if adjustment_note:
            lines.append(adjustment_note)
        lines.extend([
            f"نوع: {managed}{stale}",
            f"منبع: {rate['source']}",
            f"آخرین بروزرسانی منبع: {format_local_timestamp(parse_iso_datetime(rate.get('source_updated_at')))}",
            f"زمان دریافت/ثبت: {format_local_timestamp(parse_iso_datetime(rate.get('retrieved_at')))}",
        ])
    lines.extend([
        "",
        "⚠️ نرخ مرجع روزانه، نرخ نقدی صرافی افغانستان نیست. نرخ خرید/فروش دستی مطابق جهت جفت ارزی ثبت‌شده نمایش داده می‌شود.",
    ])
    return "\n".join(lines)


def format_managed_gold(rates: list[dict]) -> str:
    if not rates:
        return (
            "⚠️ نرخ طلا فعلاً در دسترس نیست و قیمت بازار محلی ثبت نشده است."
        )
    lines = ["🥇 قیمت طلا", "━━━━━━━━━━━━━"]
    for rate in rates:
        label = (
            "قیمت Spot جهانی، ثبت‌شده توسط مدیر"
            if rate["rate_type"] == "international_spot_manual"
            else "قیمت بازار محلی، ثبت‌شده توسط مدیر"
        )
        stale = " ⚠️ دادهٔ ذخیره‌شده و قدیمی" if rate.get("is_stale") else ""
        purity = f"، عیار/خلوص: {rate['purity']}" if rate.get("purity") else ""
        lines.extend([
            f"\n💰 قیمت هر {rate['unit']}: {_display_rate_value(rate['value'])} "
            f"{rate['quote_currency']}",
            f"نوع: {label}{stale}{purity}",
            f"منبع: {rate['source']}",
            f"زمان قیمت منبع: {format_local_timestamp(parse_iso_datetime(rate.get('source_updated_at')))}",
            f"زمان دریافت/ثبت: {format_local_timestamp(parse_iso_datetime(rate.get('retrieved_at')))}",
        ])
    lines.extend([
        "",
        "⚠️ قیمت جهانی و محلی جدا هستند؛ نرخ خودکار طلا فعال نیست و همه قیمت‌های این فهرست را مدیر ثبت کرده است.",
    ])
    return "\n".join(lines)


def parse_iso_datetime(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def format_comparison(
    reference_rates: tuple[Rate, ...],
    xe_rates: tuple[Rate, ...],
    dab_available: bool,
) -> str:
    lines = ["📈 مقایسه نرخ‌ها", "━━━━━━━━━━━━━"]
    if reference_rates:
        for rate in reference_rates:
            if rate.rate_direction == "source_unspecified":
                lines.append(
                    f"{rate.source} [{rate.rate_type}]: {_display_rate_value(rate.value)}"
                )
            elif rate.rate_direction == "quote_per_base":
                lines.append(
                    f"{rate.source}: 1 {rate.base_currency} = "
                    f"{_display_rate_value(rate.value)} {rate.quote_currency}"
                )
            elif rate.rate_direction == "base_per_quote":
                lines.append(
                    f"{rate.source}: 1 {rate.quote_currency} = "
                    f"{_display_rate_value(rate.value)} {rate.base_currency}"
                )
            else:
                lines.append(
                    f"{rate.source} [{rate.rate_type}]: {_display_rate_value(rate.value)}"
                )
    else:
        lines.append("Sarai Shahzada (sarafi.af): داده در دسترس نیست.")
    if xe_rates:
        for rate in xe_rates:
            lines.append(
                f"XE (mid-market): 1 {rate.base_currency} = "
                f"{_display_rate_value(rate.value)} {rate.quote_currency}"
            )
    else:
        lines.append("XE (mid-market): داده در دسترس نیست.")
    if not dab_available:
        lines.extend(["", "برای مقایسه رسمی DAB، منبع DAB فعلاً در دسترس نیست."])
    lines.extend(["━━━━━━━━━━━━━", "⚠️ XE نرخ میانی است و نرخ خرید/فروش DAB محسوب نمی‌شود."])
    return "\n".join(lines)
