import asyncio
import fcntl
import json
import logging
import math
import os
import re
import sqlite3
from datetime import datetime

import requests
from xml.etree import ElementTree

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
    WebAppInfo,
)
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from config import load_settings
from models import Rate
from remittance_store import RemittanceStore
from services.managed_rates import (
    DAB_REFERENCE_SOURCE_ID,
    REFERENCE_SOURCE_ID,
    ManagedRatesService,
)
from services.rates import RatesService
from utils.formatting import (
    format_comparison,
    format_managed_gold,
    format_managed_rates,
    format_xe_rates,
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
REQUEST_TIMEOUT = 15
INSTANCE_LOCK_PATH = "/tmp/telegram-bot.lock"
GRAMS_PER_TROY_OUNCE = 31.1034768
RATE_ERROR = "⚠️ فعلاً دریافت نرخ‌ها ممکن نیست. لطفاً چند لحظه بعد دوباره تلاش کنید."
GOLD_ERROR = "⚠️ اطلاعات نرخ طلا در دسترس نیست. لطفاً بعداً دوباره تلاش کنید."
MAIN_MENU_TEXT = (
    "🏦 SARWARI EXCHANGE\n\n"
    "صرافی و خدمات پولی سروری\n\n"
    "✨ اعتماد • شفافیت • سرعت\n"
    "━━━━━━━━━━━━━━━━\n\n"
    "🌿 به صرافی سروری خوش آمدید!\n\n"
    "خدمات مالی شما، ساده و در دسترس.\n\n"
    "لطفاً خدمت مورد نظر خود را انتخاب کنید:"
)
REMITS_COMMISSION_RATE = 0.04
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
WEB_APP_URL = os.getenv("WEB_APP_URL")
REMITTANCE_STORE: RemittanceStore | None = None
MANAGED_RATES: ManagedRatesService | None = None
REMITTANCE_STATUSES = (
    "🕐 در انتظار بررسی",
    "✅ تأیید شد",
    "⚙️ در حال پردازش",
    "💰 آماده دریافت",
    "✅ پرداخت شد",
    "❌ رد شد",
    "🚫 لغو شد",
)
PENDING_REMITTANCE_STATUS = "🕐 در انتظار بررسی"
REMITTANCE_STATUS_BY_KEY = {
    "processing": "⚙️ در حال پردازش",
    "ready": "💰 آماده دریافت",
    "paid": "✅ پرداخت شد",
    "cancelled": "🚫 لغو شد",
}
ACTIVE_REMITTANCE_STATUSES = {
    "🕐 در انتظار بررسی",
    "✅ تأیید شد",
    "⚙️ در حال پردازش",
    "💰 آماده دریافت",
}
TRACKING_CODE_PATTERN = re.compile(r"\bLA-\d{8}-\d+\b")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is not set")

SETTINGS = load_settings()
RATES_SERVICE = RatesService(SETTINGS)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def acquire_instance_lock():
    lock_file = open(INSTANCE_LOCK_PATH, "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_file.close()
        raise RuntimeError("Another bot.py instance is already running")
    return lock_file


def build_main_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("💱 خرید و فروش ارز", callback_data="buy_sell"),
            InlineKeyboardButton("🌍 حواله و انتقال پول", callback_data="remittance"),
        ],
        [
            InlineKeyboardButton("📊 نرخ اسعار", callback_data="rates"),
            InlineKeyboardButton("🥇 نرخ طلا", callback_data="gold"),
        ],
        [
            InlineKeyboardButton("🧾 معاملات من", callback_data="transactions"),
            InlineKeyboardButton("👤 حساب من", callback_data="account"),
        ],
        [
            InlineKeyboardButton("🆘 پشتیبانی", callback_data="support"),
            InlineKeyboardButton("ℹ️ درباره صرافی", callback_data="about"),
        ],
    ]
    return InlineKeyboardMarkup(rows)


def build_buy_sell_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🟢 خرید ارز", callback_data="buy_currency"),
                InlineKeyboardButton("🔴 فروش ارز", callback_data="sell_currency"),
            ],
            [InlineKeyboardButton("↩️ بازگشت", callback_data="main")],
        ]
    )


def build_webapp_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton("✨ منوی مدرن", web_app=WebAppInfo(url=WEB_APP_URL))]],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def build_back_keyboard(refresh_action: str | None = None) -> InlineKeyboardMarkup:
    buttons = []
    if refresh_action:
        buttons.append(InlineKeyboardButton("🔄 بروزرسانی", callback_data=refresh_action))
    buttons.append(InlineKeyboardButton("↩️ بازگشت", callback_data="main"))
    return InlineKeyboardMarkup([buttons])


def build_support_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📲 تماس با پشتیبانی در واتساپ",
                    url="https://wa.me/message/FCRIX7IXIPU7I1",
                )
            ],
            [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="main")],
        ]
    )


def build_remittance_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("💸 ارسال حواله", callback_data="remittance_send")],
            [InlineKeyboardButton("↩️ بازگشت", callback_data="main")],
        ]
    )


def build_remittance_currencies_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("EUR", callback_data="remit_currency_EUR"),
                InlineKeyboardButton("USD", callback_data="remit_currency_USD"),
            ],
            [
                InlineKeyboardButton("AFN", callback_data="remit_currency_AFN"),
                InlineKeyboardButton("GBP", callback_data="remit_currency_GBP"),
            ],
            [InlineKeyboardButton("❌ لغو درخواست", callback_data="remit_cancel")],
        ]
    )


def build_remittance_cities_keyboard() -> InlineKeyboardMarkup:
    cities = ("کابل", "هرات", "مزار شریف", "قندهار", "جلال‌آباد", "سایر")
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(city, callback_data=f"remit_city_{index}")]
            for index, city in enumerate(cities)
        ]
        + [[InlineKeyboardButton("❌ لغو درخواست", callback_data="remit_cancel")]]
    )


def build_remittance_summary_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ تأیید و ارسال به مدیریت", callback_data="remit_confirm")],
            [InlineKeyboardButton("✏️ اصلاح اطلاعات", callback_data="remittance_send")],
            [InlineKeyboardButton("❌ لغو درخواست", callback_data="remit_cancel")],
        ]
    )


def build_account_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📋 حواله‌های من", callback_data="account_remittances"),
                InlineKeyboardButton("🔎 پیگیری حواله", callback_data="account_track"),
            ],
            [
                InlineKeyboardButton("💰 حواله فعال", callback_data="account_active"),
                InlineKeyboardButton("🧾 سوابق معاملات", callback_data="account_history"),
            ],
            [
                InlineKeyboardButton("💬 پشتیبانی", callback_data="account_support"),
                InlineKeyboardButton("🔐 امنیت حساب", callback_data="account_security"),
            ],
            [InlineKeyboardButton("⬅️ بازگشت", callback_data="account_main")],
        ]
    )


def calculate_remittance_totals(amount: float) -> tuple[float, float]:
    commission = round(amount * REMITS_COMMISSION_RATE, 2)
    return commission, round(amount + commission, 2)


def get_customer_code(customer_chat_id: int) -> str:
    return get_remittance_store().get_or_create_customer_code(customer_chat_id)


def get_remittance_store() -> RemittanceStore:
    if REMITTANCE_STORE is None:
        raise RuntimeError("Remittance storage has not been initialized")
    return REMITTANCE_STORE


def get_managed_rates() -> ManagedRatesService:
    if MANAGED_RATES is None:
        raise RuntimeError("Rate management has not been initialized")
    return MANAGED_RATES


def get_customer_remittances(customer_chat_id: int) -> list[dict]:
    return get_remittance_store().get_customer_remittances(customer_chat_id)


def format_account_panel(customer_name: str, customer_chat_id: int) -> str:
    requests = get_customer_remittances(customer_chat_id)
    pending_count = sum(request.get("status") == "🕐 در انتظار بررسی" for request in requests)
    processing_count = sum(request.get("status") == "⚙️ در حال پردازش" for request in requests)
    paid_count = sum(request.get("status") == "✅ پرداخت شد" for request in requests)
    return (
        "👤 حساب کاربری من\n"
        "━━━━━━━━━━━━━━━\n"
        f"سلام، {customer_name} عزیز 👋\n"
        f"🆔 کد مشتری: {get_customer_code(customer_chat_id)}\n"
        "🟢 وضعیت حساب: فعال\n\n"
        "💸 خلاصه فعالیت\n"
        f"📋 کل حواله‌ها: {len(requests)}\n"
        f"🕐 در حال بررسی: {pending_count}\n"
        f"⚙️ در حال پردازش: {processing_count}\n"
        f"✅ پرداخت‌شده: {paid_count}\n"
        "━━━━━━━━━━━━━━━━\n"
        "🔐 اطلاعات شما محرمانه است."
    )


def format_customer_remittance(request: dict) -> str:
    return (
        f"🆔 کد پیگیری: {request['tracking_code']}\n"
        f"💰 مبلغ: {request['amount']:g} {request['currency']}\n"
        f"📍 شهر: {request['city']}\n"
        f"🕐 وضعیت: {request.get('status', '🕐 در انتظار بررسی')}"
    )


def format_remittance_summary(request: dict) -> str:
    commission, total = calculate_remittance_totals(request["amount"])
    return (
        "🧾 خلاصه درخواست حواله\n\n"
        f"👤 فرستنده: {request['sender']}\n"
        f"👤 گیرنده: {request['recipient']}\n"
        f"💰 مبلغ: {request['amount']:g}\n"
        f"💱 واحد پول: {request['currency']}\n"
        f"📍 محل دریافت: {request['city']}\n"
        f"💳 کمیشن: 4٪ = {commission:g} {request['currency']}\n"
        f"💵 مجموع: {total:g} {request['currency']}\n"
        "🪪 مدرک گیرنده: ✅ دریافت شد"
    )


def format_admin_remittance(request: dict) -> str:
    commission, total = calculate_remittance_totals(request["amount"])
    return (
        "🔔 درخواست جدید حواله\n\n"
        f"🆔 کد پیگیری: {request['tracking_code']}\n"
        f"👤 فرستنده: {request['sender']}\n"
        f"👤 گیرنده: {request['recipient']}\n"
        f"💰 مبلغ: {request['amount']:g}\n"
        f"💱 واحد پول: {request['currency']}\n"
        f"📍 شهر: {request['city']}\n"
        f"💳 کمیشن: 4٪ = {commission:g} {request['currency']}\n"
        f"💵 مجموع: {total:g} {request['currency']}\n"
        "🪪 مدرک گیرنده: پیوست شده\n"
        f"🕐 وضعیت: {request.get('status', '🕐 در انتظار بررسی')}"
    )


def build_admin_remittance_keyboard(tracking_code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ تأیید حواله", callback_data=f"admin_approve_{tracking_code}"),
                InlineKeyboardButton("❌ رد حواله", callback_data=f"admin_reject_{tracking_code}"),
            ],
            [InlineKeyboardButton("💬 درخواست اطلاعات بیشتر", callback_data=f"admin_more_{tracking_code}")],
        ]
    )


def build_admin_status_keyboard(tracking_code: str, status: str) -> InlineKeyboardMarkup:
    next_buttons = {
        "✅ تأیید شد": [
            InlineKeyboardButton("⚙️ در حال پردازش", callback_data=f"admin_status_processing_{tracking_code}"),
            InlineKeyboardButton("🚫 لغو حواله", callback_data=f"admin_status_cancelled_{tracking_code}"),
        ],
        "⚙️ در حال پردازش": [
            InlineKeyboardButton("💰 آماده دریافت", callback_data=f"admin_status_ready_{tracking_code}"),
            InlineKeyboardButton("🚫 لغو حواله", callback_data=f"admin_status_cancelled_{tracking_code}"),
        ],
        "💰 آماده دریافت": [
            InlineKeyboardButton("✅ پرداخت شد", callback_data=f"admin_status_paid_{tracking_code}"),
            InlineKeyboardButton("🚫 لغو حواله", callback_data=f"admin_status_cancelled_{tracking_code}"),
        ],
    }
    rows = [next_buttons[status]] if status in next_buttons else []
    rows.append([InlineKeyboardButton("💬 پیام به مشتری", callback_data=f"admin_message_{tracking_code}")])
    return InlineKeyboardMarkup(rows)


def build_rate_management_keyboard() -> InlineKeyboardMarkup:
    store = get_remittance_store()
    reference_label = (
        "خاموش‌کردن نرخ مرجع"
        if store.is_source_enabled(REFERENCE_SOURCE_ID)
        else "روشن‌کردن نرخ مرجع"
    )
    dab_label = (
        "خاموش‌کردن نرخ Indicative DAB"
        if store.is_source_enabled(DAB_REFERENCE_SOURCE_ID)
        else "روشن‌کردن نرخ Indicative DAB"
    )
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 مشاهده نرخ ارز و طلا", callback_data="admin_rates_view")],
        [InlineKeyboardButton("➕ افزودن/ویرایش نرخ ارز", callback_data="admin_rates_add_currency")],
        [InlineKeyboardButton("🥇 افزودن/ویرایش نرخ دستی طلا", callback_data="admin_rates_add_gold")],
        [InlineKeyboardButton("↩️ بازگشت به نرخ خودکار", callback_data="admin_rates_restore")],
        [
            InlineKeyboardButton(reference_label, callback_data="admin_rates_toggle_frankfurter"),
            InlineKeyboardButton(dab_label, callback_data="admin_rates_toggle_frankfurter_dab"),
        ],
        [InlineKeyboardButton("🧾 تاریخچه تغییرات", callback_data="admin_rates_audit")],
        [InlineKeyboardButton("❌ لغو ورود اطلاعات", callback_data="admin_rates_cancel_input")],
    ])


def is_admin_user(user_id: int | None) -> bool:
    return bool(ADMIN_CHAT_ID) and str(user_id) == ADMIN_CHAT_ID


def get_active_customer_remittances(customer_chat_id: int) -> list[dict]:
    return [
        request
        for request in get_customer_remittances(customer_chat_id)
        if request.get("status") in ACTIVE_REMITTANCE_STATUSES
    ]


def extract_tracking_code(text: str) -> str | None:
    match = TRACKING_CODE_PATTERN.search(text)
    return match.group(0) if match else None


def set_remittance_status(tracking_code: str, status_key: str) -> dict | None:
    status = REMITTANCE_STATUS_BY_KEY.get(status_key)
    allowed_previous_statuses = {
        "processing": {"✅ تأیید شد"},
        "ready": {"✅ تأیید شد", "⚙️ در حال پردازش"},
        "paid": {"💰 آماده دریافت"},
        "cancelled": {"✅ تأیید شد", "⚙️ در حال پردازش", "💰 آماده دریافت"},
    }
    expected_statuses = allowed_previous_statuses.get(status_key, set())
    if not status or not expected_statuses:
        return None
    return get_remittance_store().transition_status(
        tracking_code,
        expected_statuses=expected_statuses,
        new_status=status,
    )


def clear_remittance(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("remittance", None)


def format_about_message() -> str:
    return (
        "🏦 صرافی و خدمات پولی سروری\n"
        "SARWARI EXCHANGE\n"
        "━━━━━━━━━━━━━\n"
        "🔐 امنیت | ⚡ سرعت | 🤝 اعتماد\n\n"
        "ما در زمینه خدمات صرافی و انتقال پول فعالیت می‌کنیم و تلاش داریم خدمات سریع، شفاف و قابل اعتماد را برای مشتریان ارائه کنیم.\n\n"
        "📍 آدرس:\n"
        "کابل، کوته‌سنگی، مارکیت آریانا کابل،\n"
        "منزل اول، دکان ۶۴\n\n"
        "📞 برای دریافت نرخ و خدمات بیشتر با پشتیبانی تماس بگیرید."
    )


def get_news() -> list[tuple[str, str, str]]:
    feeds = [
        ("BBC", "https://feeds.bbci.co.uk/news/world/rss.xml"),
        ("DW", "https://rss.dw.com/rdf/rss-en-world"),
        ("NPR", "https://feeds.npr.org/1004/rss.xml"),
    ]

    news: list[tuple[str, str, str]] = []
    seen_titles: set[str] = set()

    for source, feed_url in feeds:
        try:
            response = requests.get(feed_url, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            root = ElementTree.fromstring(response.content)
            for entry in root.findall(".//item")[:4]:
                title = entry.findtext("title", default="").strip()
                link = entry.findtext("link", default="").strip()
                if title and link and title not in seen_titles:
                    news.append((source, title, link))
                    seen_titles.add(title)
        except (requests.RequestException, ElementTree.ParseError, ValueError):
            continue

    return news[:8]


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(MAIN_MENU_TEXT, reply_markup=build_main_keyboard())
    if WEB_APP_URL and update.effective_chat and update.effective_chat.type == "private":
        await update.message.reply_text(
            "✨ برای باز کردن منوی مدرن، دکمه زیر را بزنید.",
            reply_markup=build_webapp_keyboard(),
        )


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat and update.effective_chat.type == "private":
        await update.message.reply_text(str(update.effective_user.id))


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "ℹ️ درباره ربات\n"
        "━━━━━━━━━━━━━\n"
        "/start - منوی اصلی\n"
        "/rates - نرخ دالر، یورو و ارزهای منطقه\n"
        "/gold - نرخ تقریبی طلا به ازای هر گرام\n"
        "/news - آخرین اخبار مهم بین‌المللی\n"
        "/help - راهنما\n"
        "/news - اخبار روز"
    )
    await update.message.reply_text(text, reply_markup=build_back_keyboard())


async def admin_rates_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not is_admin_user(update.effective_user.id):
        await update.effective_message.reply_text("دسترسی مجاز نیست.")
        return
    await show_rate_management(update, context)


async def show_rate_management(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    text: str | None = None,
    edit: bool = False,
):
    if text is None:
        text = (
            "⚙️ مدیریت نرخ‌ها\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "Frankfurter نرخ‌های مرجع روزانه دارد؛ نرخ جداگانه خرید/فروش نقدی و حواله DAB از API مستند در دسترس نیست.\n"
            "قیمت طلا به‌صورت خودکار غیرفعال است، چون واحد قیمت منبع مستند نشده؛ طلا را دستی با واحد و عیار ثبت کنید."
        )
    keyboard = build_rate_management_keyboard()
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=keyboard)
    elif update.effective_message:
        await update.effective_message.reply_text(text, reply_markup=keyboard)


async def rate_management_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    if not is_admin_user(query.from_user.id):
        await query.answer("دسترسی مجاز نیست.", show_alert=True)
        return
    await query.answer()

    action = query.data.removeprefix("admin_rates_")
    if action == "view":
        await get_managed_rates().refresh_all()
        currencies = await get_managed_rates().get_effective_rates()
        gold = await get_managed_rates().get_effective_gold()
        text = format_managed_rates(currencies) + "\n\n" + format_managed_gold(gold)
        await show_rate_management(update, context, text=text, edit=True)
        return
    if action == "add_currency":
        context.user_data["rate_management_input"] = "currency"
        await query.edit_message_text(
            "➕ نرخ ارز را در یک خط وارد کنید:\n"
            "BASE QUOTE TYPE VALUE [UNIT]\n"
            "نمونه: USD AFN cash_buy 65.5 AFN per USD\n"
            "انواع: cash_buy, cash_sell, transfer_buy, transfer_sell, mid_market, spot\n"
            "جفت‌ارز مطابق همان جهتی ذخیره می‌شود که وارد می‌کنید.",
            reply_markup=build_rate_management_keyboard(),
        )
        return
    if action == "add_gold":
        context.user_data["rate_management_input"] = "gold"
        await query.edit_message_text(
            "🥇 قیمت طلا را وارد کنید:\n"
            "VALUE CURRENCY UNIT [PURITY]\n"
            "نمونهٔ طلا‌ی محلی: 8500 AFN gram 21K\n"
            "نمونهٔ اونس: 4000 USD troy_ounce spot\n"
            "قیمت دستی به‌عنوان قیمت محلی فقط با همین برچسب نشان داده می‌شود.",
            reply_markup=build_rate_management_keyboard(),
        )
        return
    if action == "restore":
        context.user_data["rate_management_input"] = "restore"
        await query.edit_message_text(
            "برای حذف بازنویسی دستی و بازگشت به منبع خودکار، یکی را وارد کنید:\n"
            "ارز: BASE QUOTE TYPE، نمونه: USD AFN cash_buy\n"
            "طلا: GOLD UNIT CURRENCY [PURITY]، نمونه: GOLD troy_ounce USD spot",
            reply_markup=build_rate_management_keyboard(),
        )
        return
    if action == "cancel_input":
        context.user_data.pop("rate_management_input", None)
        await show_rate_management(update, context, edit=True)
        return
    if action.startswith("toggle_"):
        source_id = action.removeprefix("toggle_")
        if source_id not in {REFERENCE_SOURCE_ID, DAB_REFERENCE_SOURCE_ID}:
            await query.answer("منبع خودکار پشتیبانی نمی‌شود.", show_alert=True)
            return
        store = get_remittance_store()
        enabled = not store.is_source_enabled(source_id)
        store.set_source_enabled(source_id, enabled, query.from_user.id)
        status = "فعال شد" if enabled else "غیرفعال شد"
        await show_rate_management(
            update,
            context,
            text=f"✅ منبع {source_id} {status}.",
            edit=True,
        )
        return
    if action == "audit":
        history = get_remittance_store().get_rate_audit_history(limit=8)
        lines = ["🧾 تاریخچه آخرین تغییرات نرخ", "━━━━━━━━━━━━━━━━━━"]
        for item in history:
            lines.append(
                f"\n{item['action']} | {item['rate_key']}\n"
                f"مدیر: {item['administrator_id']} | زمان: {item['changed_at']}\n"
                f"پیشین: {item['old_value_json'] or 'نداشت'}\n"
                f"جدید: {item['new_value_json'] or 'ندارد'}"
            )
        await show_rate_management(update, context, text="\n".join(lines), edit=True)
        return
    await query.answer("گزینه مدیریت نرخ شناخته نشد.", show_alert=True)


async def handle_rate_management_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    operation = context.user_data.get("rate_management_input")
    if not operation:
        return False
    if not update.effective_user or not is_admin_user(update.effective_user.id):
        context.user_data.pop("rate_management_input", None)
        await update.effective_message.reply_text("دسترسی مجاز نیست.")
        return True
    if not update.effective_message or not update.effective_message.text:
        await update.effective_message.reply_text("لطفاً اطلاعات را به شکل متن ارسال کنید.")
        return True

    parts = update.effective_message.text.strip().split()
    try:
        store = get_remittance_store()
        admin_id = update.effective_user.id
        if operation == "currency":
            if len(parts) < 4:
                raise ValueError("فرمت نرخ ارز ناقص است.")
            base, quote, rate_type = parts[0].upper(), parts[1].upper(), parts[2]
            if not re.fullmatch(r"[A-Z]{3}", base) or not re.fullmatch(r"[A-Z]{3}", quote):
                raise ValueError("کد ارز باید سه حرف انگلیسی باشد.")
            if rate_type not in {
                "cash_buy", "cash_sell", "transfer_buy", "transfer_sell",
                "mid_market", "spot",
            }:
                raise ValueError("نوع نرخ شناخته نشد.")
            value = float(parts[3])
            unit = " ".join(parts[4:]) if len(parts) > 4 else quote
            key = store.fx_rate_key(base, quote, rate_type)
            action = store.set_manual_rate(
                rate_key=key,
                item_kind="currency",
                base_currency=base,
                quote_currency=quote,
                rate_type=rate_type,
                value=value,
                unit=unit,
                purity=None,
                administrator_id=admin_id,
            )
            result = f"✅ نرخ {base}/{quote} ({rate_type}) به‌صورت دستی ثبت شد: {action}."
        elif operation == "gold":
            if len(parts) < 3:
                raise ValueError("فرمت نرخ طلا ناقص است.")
            value = float(parts[0])
            currency = parts[1].upper()
            unit = parts[2].replace("_", " ")
            purity = " ".join(parts[3:]) or None
            if not re.fullmatch(r"[A-Z]{3}", currency):
                raise ValueError("کد ارز باید سه حرف انگلیسی باشد.")
            key = store.gold_rate_key(unit, currency, purity)
            action = store.set_manual_rate(
                rate_key=key,
                item_kind="gold",
                base_currency="XAU",
                quote_currency=currency,
                rate_type="local_market_manual",
                value=value,
                unit=unit,
                purity=purity,
                administrator_id=admin_id,
            )
            result = f"✅ نرخ دستی طلا ثبت شد ({action})."
        elif operation == "restore":
            if len(parts) >= 3 and parts[0].upper() == "GOLD":
                unit = parts[1].replace("_", " ")
                currency = parts[2].upper()
                purity = " ".join(parts[3:]) or None
                key = store.gold_rate_key(unit, currency, purity)
            elif len(parts) == 3:
                base, quote, rate_type = parts[0].upper(), parts[1].upper(), parts[2]
                key = store.fx_rate_key(base, quote, rate_type)
            else:
                raise ValueError("فرمت شناسایی نرخ درست نیست.")
            if not store.restore_automatic_rate(key, admin_id):
                result = "⚠️ بازنویسی دستی برای این مورد پیدا نشد."
            else:
                result = (
                    "✅ بازنویسی دستی برداشته شد. اگر برای این جفت نرخ خودکار موجود نباشد، "
                    "باید آن را دستی وارد کنید."
                )
        else:
            return False
        context.user_data.pop("rate_management_input", None)
        await show_rate_management(update, context, text=result)
    except (ValueError, sqlite3.Error) as error:
        if isinstance(error, sqlite3.Error):
            logging.exception("Rate management storage operation failed")
            message = "⚠️ ذخیره نرخ انجام نشد. لطفاً بعداً دوباره تلاش کنید."
        else:
            message = f"⚠️ {error}"
        await update.effective_message.reply_text(
            message,
            reply_markup=build_rate_management_keyboard(),
        )
    return True


async def show_rates(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    edit: bool = False,
    force_refresh: bool = False,
):
    try:
        if force_refresh:
            await get_managed_rates().refresh_reference(force=True)
        rates = await get_managed_rates().get_effective_rates()
        text = format_managed_rates(rates) if rates else RATE_ERROR
    except (requests.RequestException, ValueError, TypeError, KeyError, sqlite3.Error) as error:
        logging.warning("Currency API failed: %s", error)
        text = RATE_ERROR

    keyboard = build_back_keyboard("refresh")
    if edit and update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, reply_markup=keyboard)
        except BadRequest as error:
            if "Message is not modified" not in str(error):
                raise
    else:
        await update.message.reply_text(text, reply_markup=keyboard)


async def rates(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_rates(update, context)


def _rate_datetime(value: str | None) -> datetime:
    if value is None:
        return datetime.now().astimezone()
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


async def show_comparison(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False):
    try:
        managed_reference, xe_result, dab_result = await asyncio.gather(
            get_managed_rates().get_effective_rates(),
            RATES_SERVICE.get_xe(),
            RATES_SERVICE.get_dab(),
        )
        reference_rates = tuple(
            Rate(
                symbol=rate["rate_key"],
                base_currency=rate["base_currency"],
                quote_currency=rate["quote_currency"],
                value=rate["value"],
                source=rate["source"],
                rate_type=rate["rate_type"],
                timestamp=_rate_datetime(
                    rate.get("source_updated_at") or rate.get("retrieved_at")
                ),
                is_stale=rate.get("is_stale", False),
                rate_direction="quote_per_base",
                source_unit=rate["unit"],
                retrieved_at=_rate_datetime(rate.get("retrieved_at")),
                source_updated_at=_rate_datetime(rate.get("source_updated_at")),
            )
            for rate in managed_reference
        )
        text = format_comparison(
            reference_rates,
            xe_result.rates,
            dab_result.available,
        )
    except (
        requests.RequestException, ValueError, TypeError, KeyError,
        ZeroDivisionError, sqlite3.Error,
    ) as error:
        logging.warning("Comparison API failed: %s", error)
        text = RATE_ERROR

    keyboard = build_back_keyboard("compare")
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=keyboard)
    else:
        await update.message.reply_text(text, reply_markup=keyboard)


async def show_gold(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    edit: bool = False,
    force_refresh: bool = False,
):
    try:
        if force_refresh:
            await get_managed_rates().refresh_gold(force=True)
        rates = await get_managed_rates().get_effective_gold()
        text = format_managed_gold(rates) if rates else GOLD_ERROR
    except (requests.RequestException, ValueError, TypeError, KeyError, sqlite3.Error) as error:
        logging.warning("Gold API failed: %s", error)
        text = GOLD_ERROR

    keyboard = build_back_keyboard()
    if edit and update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, reply_markup=keyboard)
        except BadRequest as error:
            if "Message is not modified" not in str(error):
                raise
    else:
        await update.message.reply_text(text, reply_markup=keyboard)


async def gold(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_gold(update, context)


async def show_news(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False):
    try:
        items = await asyncio.to_thread(get_news)
    except (requests.RequestException, ValueError) as error:
        logging.warning("News feeds failed: %s", error)
        items = []

    if not items:
        text = "⚠️ فعلاً دریافت اخبار ممکن نیست. لطفاً چند لحظه بعد دوباره تلاش کنید."
    else:
        text = "📰 اخبار مهم بین‌المللی\n\n"
        for index, (source, title, link) in enumerate(items, 1):
            text += f"{index}. {title}\nمنبع: {source}\n{link}\n\n"

    keyboard = build_back_keyboard("news")
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=keyboard)
    else:
        await update.message.reply_text(text, reply_markup=keyboard)


async def news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_news(update, context)


async def web_app_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message or not update.effective_message.web_app_data:
        return
    try:
        payload = json.loads(update.effective_message.web_app_data.data)
    except (TypeError, ValueError, json.JSONDecodeError):
        await update.effective_message.reply_text("⚠️ درخواست منوی مدرن معتبر نیست.")
        return

    action = payload.get("action") if isinstance(payload, dict) else None
    if action == "rates":
        await show_rates(update, context)
    elif action == "gold":
        await show_gold(update, context)
    elif action == "account":
        await show_account(update, context)
    elif action == "remittance":
        await update.effective_message.reply_text(
            "🌍 حواله و انتقال پول\n\nبرای ثبت درخواست گزینه زیر را انتخاب کنید.",
            reply_markup=build_remittance_menu_keyboard(),
        )
    elif action == "about":
        await update.effective_message.reply_text(
            format_about_message(), reply_markup=build_back_keyboard()
        )
    elif action == "support":
        await update.effective_message.reply_text(
            "🆘 پشتیبانی SARWARI EXCHANGE\n\n"
            "برای دریافت راهنمایی، استعلام نرخ، پیگیری خدمات و ارتباط با صرافی، از طریق واتساپ با ما در تماس باشید.",
            reply_markup=build_support_keyboard(),
        )
    elif action == "buy_sell":
        await update.effective_message.reply_text(
            "💱 خرید و فروش ارز\n\nلطفاً نوع معاملهٔ مورد نظر را انتخاب کنید.",
            reply_markup=build_buy_sell_keyboard(),
        )
    elif action == "transactions":
        await update.effective_message.reply_text(
            "🚧 این بخش به‌زودی فعال خواهد شد.", reply_markup=build_back_keyboard()
        )
    else:
        await update.effective_message.reply_text("⚠️ گزینه منوی مدرن شناخته نشد.")


async def show_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    try:
        text = format_account_panel(user.full_name, user.id)
    except sqlite3.Error:
        logging.exception("Could not load customer account from SQLite")
        text = "⚠️ دریافت اطلاعات حساب فعلاً ممکن نیست. لطفاً دوباره تلاش کنید."
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=build_account_keyboard())
    else:
        await update.message.reply_text(text, reply_markup=build_account_keyboard())


async def show_customer_remittances(update: Update, context: ContextTypes.DEFAULT_TYPE, active_only: bool = False):
    requests = get_customer_remittances(update.callback_query.from_user.id)
    if active_only:
        requests = [
            request for request in requests if request.get("status") in ACTIVE_REMITTANCE_STATUSES
        ]
    title = "💰 حواله فعال" if active_only else "📋 حواله‌های من"
    if requests:
        text = f"{title}\n━━━━━━━━━━━━━━━\n\n" + "\n\n".join(
            format_customer_remittance(request) for request in requests
        )
    else:
        text = f"{title}\n━━━━━━━━━━━━━━━\n\n⚠️ موردی برای نمایش وجود ندارد."
    try:
        await update.callback_query.edit_message_text(text, reply_markup=build_account_keyboard())
    except BadRequest as error:
        if "Message is not modified" not in str(error):
            raise


async def show_customer_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_customer_remittances(update, context)


async def handle_account_tracking_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("account_tracking", None)
    tracking_code = extract_tracking_code(update.message.text.strip())
    if not tracking_code:
        await update.message.reply_text("⚠️ کد پیگیری معتبر نیست. نمونه: LA-20260923-1001")
        return
    requests = get_customer_remittances(update.effective_user.id)
    request = next(
        (item for item in requests if item.get("tracking_code") == tracking_code),
        None,
    )
    if not request:
        await update.message.reply_text("⚠️ حواله‌ای با این کد پیگیری برای حساب شما پیدا نشد.")
        return
    await update.message.reply_text(format_customer_remittance(request))


async def start_remittance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    request = {"step": "sender"}
    get_remittance_store().save_draft(update.callback_query.from_user.id, request)
    context.user_data["remittance"] = request
    await update.callback_query.edit_message_text(
        "💸 ارسال حواله\n\n👤 نام فرستنده را وارد کنید:",
        reply_markup=build_back_keyboard("remit_cancel"),
    )


async def handle_customer_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    account_support = context.user_data.pop("account_support", False)
    if not update.message or not update.message.text or not ADMIN_CHAT_ID:
        return

    active_requests = get_active_customer_remittances(update.effective_user.id)
    if not active_requests:
        if account_support:
            await update.message.reply_text("⚠️ برای اتصال پیام پشتیبانی، حواله فعال پیدا نشد.")
        return

    tracking_code = extract_tracking_code(update.message.text)
    if len(active_requests) > 1 and not tracking_code:
        await update.message.reply_text(
            "⚠️ چند حواله فعال دارید. لطفاً کد پیگیری حواله را در پیام خود وارد کنید."
        )
        return

    if tracking_code:
        matching_requests = [
            request for request in active_requests if request.get("tracking_code") == tracking_code
        ]
        if not matching_requests:
            await update.message.reply_text("⚠️ کد پیگیری معتبر یا فعال پیدا نشد.")
            return
        request = matching_requests[0]
    else:
        request = active_requests[0]

    await context.bot.send_message(
        chat_id=int(ADMIN_CHAT_ID),
        text=(
            "💬 پیام مشتری درباره حواله\n"
            f"🆔 کد پیگیری: {request['tracking_code']}\n"
            f"👤 فرستنده: {request['sender']}\n\n"
            f"پیام مشتری:\n{update.message.text}"
        ),
    )
    await update.message.reply_text(
        f"✅ پیام شما برای مدیریت ارسال شد.\n🆔 کد پیگیری: {request['tracking_code']}"
    )


async def handle_admin_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    tracking_code = context.user_data.pop("admin_message_tracking_code", None)
    if not tracking_code:
        return False

    if not is_admin_user(update.effective_user.id) or not update.message or not update.message.text:
        return True

    request = get_remittance_store().get_remittance(tracking_code)
    if not request:
        await update.message.reply_text("⚠️ حواله پیدا نشد.")
        return True

    await context.bot.send_message(
        chat_id=request["customer_chat_id"],
        text=(
            "💬 پیام مدیریت\n"
            f"🆔 کد پیگیری: {tracking_code}\n\n"
            f"{update.message.text}"
        ),
    )
    await update.message.reply_text("✅ پیام فقط برای مشتری همین حواله ارسال شد.")
    return True


async def remittance_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    request = context.user_data.get("remittance")
    if await handle_admin_message(update, context):
        return
    if await handle_rate_management_input(update, context):
        return

    if context.user_data.get("account_tracking"):
        await handle_account_tracking_message(update, context)
        return

    if not request:
        request = get_remittance_store().get_draft(update.effective_user.id)
        if request:
            context.user_data["remittance"] = request
    if not request:
        await handle_customer_message(update, context)
        return

    if request["step"] == "document":
        if not update.message.photo:
            await update.message.reply_text("🪪 لطفاً عکس تذکره یا پاسپورت گیرنده را ارسال کنید.")
            return
        updated_request = dict(request)
        updated_request["document_file_id"] = update.message.photo[-1].file_id
        updated_request["step"] = "summary"
        get_remittance_store().save_draft(update.effective_user.id, updated_request)
        context.user_data["remittance"] = updated_request
        await update.message.reply_text(
            format_remittance_summary(updated_request),
            reply_markup=build_remittance_summary_keyboard(),
        )
        return

    if not update.message.text:
        return
    value = update.message.text.strip()
    step = request["step"]
    if not value:
        await update.message.reply_text("⚠️ این مقدار نمی‌تواند خالی باشد. دوباره تلاش کنید.")
    elif step == "sender":
        updated_request = dict(request)
        updated_request["sender"] = value
        updated_request["step"] = "recipient"
        get_remittance_store().save_draft(update.effective_user.id, updated_request)
        context.user_data["remittance"] = updated_request
        await update.message.reply_text("👤 نام گیرنده را وارد کنید:")
    elif step == "recipient":
        updated_request = dict(request)
        updated_request["recipient"] = value
        updated_request["step"] = "amount"
        get_remittance_store().save_draft(update.effective_user.id, updated_request)
        context.user_data["remittance"] = updated_request
        await update.message.reply_text("💰 مبلغ حواله را وارد کنید (مثلاً 500):")
    elif step == "amount":
        try:
            amount = float(value.replace(",", ""))
            if not math.isfinite(amount) or amount <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("⚠️ مبلغ معتبر نیست. یک عدد مثبت وارد کنید.")
            return
        updated_request = dict(request)
        updated_request["amount"] = amount
        updated_request["step"] = "currency"
        get_remittance_store().save_draft(update.effective_user.id, updated_request)
        context.user_data["remittance"] = updated_request
        await update.message.reply_text(
            "💱 واحد پول را انتخاب کنید:", reply_markup=build_remittance_currencies_keyboard()
        )


async def confirm_remittance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    request = context.user_data.get("remittance")
    if not request:
        request = get_remittance_store().get_draft(update.callback_query.from_user.id)
        if request:
            context.user_data["remittance"] = request
    if not request or request.get("step") != "summary":
        await update.callback_query.answer("درخواست کامل نیست.", show_alert=True)
        return

    pending_request = dict(request)
    pending_request["customer_chat_id"] = update.callback_query.from_user.id
    try:
        saved_request = get_remittance_store().create_remittance(
            pending_request,
            initial_status=PENDING_REMITTANCE_STATUS,
            commission_rate=REMITS_COMMISSION_RATE,
        )
    except (sqlite3.Error, ValueError):
        logging.exception("Could not persist new remittance")
        message = update.effective_message
        text = "⚠️ ثبت حواله ممکن نشد. اطلاعات شما محفوظ است؛ لطفاً دوباره تلاش کنید."
        if message:
            await message.reply_text(text)
        else:
            await update.callback_query.answer(text, show_alert=True)
        return

    tracking_code = saved_request["tracking_code"]
    request.update(saved_request)
    clear_remittance(context)

    if ADMIN_CHAT_ID:
        try:
            await context.bot.send_photo(
                chat_id=int(ADMIN_CHAT_ID),
                photo=request["document_file_id"],
                caption=format_admin_remittance(request),
                reply_markup=build_admin_remittance_keyboard(tracking_code),
            )
        except (ValueError, BadRequest) as error:
            logging.warning("Could not notify remittance administrator: %s", error)
    else:
        logging.warning("ADMIN_CHAT_ID is not configured; remittance is stored without notification")

    await update.callback_query.edit_message_text(
        "✅ درخواست حواله ثبت شد.\n\n"
        f"🆔 کد پیگیری: {tracking_code}\n"
        "🕐 وضعیت: در انتظار بررسی\n\n"
        "پس از بررسی مدیریت، نتیجه برای شما ارسال می‌شود.",
        reply_markup=build_back_keyboard(),
    )


async def admin_remittance_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin_user(query.from_user.id):
        await query.answer("دسترسی مجاز نیست.", show_alert=True)
        return

    if query.data.startswith("admin_status_"):
        status_key, tracking_code = query.data[len("admin_status_"):].rsplit("_", 1)
        request = set_remittance_status(tracking_code, status_key)
        if not request:
            current = get_remittance_store().get_remittance(tracking_code)
            if not current:
                await query.answer("حواله پیدا نشد.", show_alert=True)
            else:
                await query.answer(
                    f"این تغییر وضعیت در مرحله فعلی مجاز نیست: {current['status']}",
                    show_alert=True,
                )
            return
        await context.bot.send_message(
            chat_id=request["customer_chat_id"],
            text=(
                "🔔 وضعیت حواله شما تغییر کرد.\n"
                f"🆔 کد پیگیری: {tracking_code}\n"
                f"🕐 وضعیت: {request['status']}"
            ),
        )
        await query.edit_message_caption(
            caption=format_admin_remittance(request),
            reply_markup=build_admin_status_keyboard(tracking_code, request["status"]),
        )
        await query.answer("وضعیت و مشتری به‌روزرسانی شدند.")
        return

    if query.data.startswith("admin_message_"):
        tracking_code = query.data[len("admin_message_"):]
        request = get_remittance_store().get_remittance(tracking_code)
        if not request:
            await query.answer("حواله پیدا نشد.", show_alert=True)
            return
        context.user_data["admin_message_tracking_code"] = tracking_code
        await query.answer("متن پیام را ارسال کنید.")
        await context.bot.send_message(
            chat_id=query.from_user.id,
            text=f"💬 متن پیام برای مشتری حواله {tracking_code} را ارسال کنید:",
        )
        return

    if query.data.startswith("admin_approve_"):
        action = "approve"
        tracking_code = query.data[len("admin_approve_"):]
    elif query.data.startswith("admin_reject_"):
        action = "reject"
        tracking_code = query.data[len("admin_reject_"):]
    elif query.data.startswith("admin_more_"):
        action = "more"
        tracking_code = query.data[len("admin_more_"):]
    else:
        await query.answer("دستور مدیریتی ناشناخته است.", show_alert=True)
        return

    store = get_remittance_store()
    request = store.get_remittance(tracking_code)
    if not request:
        await query.answer("درخواست پیدا نشد.", show_alert=True)
        return

    if action in {"approve", "reject"}:
        decision_status = "✅ تأیید شد" if action == "approve" else "❌ رد شد"
        request = store.transition_status(
            tracking_code,
            expected_statuses={PENDING_REMITTANCE_STATUS},
            new_status=decision_status,
        )
        if not request:
            current = store.get_remittance(tracking_code)
            if not current:
                await query.answer("درخواست پیدا نشد.", show_alert=True)
            else:
                await query.answer(
                    f"این حواله قبلاً بررسی شده است و وضعیت فعلی آن: {current['status']}",
                    show_alert=True,
                )
            return

    if action == "approve":
        customer_text = (
            f"✅ حواله شما با کد {tracking_code} توسط مدیریت تأیید شد.\n"
            "وضعیت: ✅ تأیید شد\n\n"
            "این نمونه آزمایشی پرداخت یا انتقال واقعی انجام نمی‌دهد."
        )
        await context.bot.send_message(chat_id=request["customer_chat_id"], text=customer_text)
        await query.edit_message_caption(
            caption=format_admin_remittance(request),
            reply_markup=build_admin_status_keyboard(tracking_code, request["status"]),
        )
    elif action == "reject":
        await context.bot.send_message(
            chat_id=request["customer_chat_id"],
            text=f"❌ حواله شما با کد {tracking_code} رد شد.\nوضعیت: ❌ رد شد",
        )
        await query.edit_message_caption(caption=format_admin_remittance(request), reply_markup=None)
    elif action == "more":
        await context.bot.send_message(
            chat_id=request["customer_chat_id"],
            text=(
                f"💬 مدیریت درباره حواله {tracking_code} اطلاعات بیشتری درخواست کرده است.\n"
                "لطفاً برای پیگیری با پشتیبانی تماس بگیرید.\n"
                "🕐 وضعیت: در انتظار بررسی"
            ),
        )
        await query.answer("درخواست اطلاعات بیشتر برای مشتری ارسال شد.")
        return
    await query.answer("وضعیت درخواست به‌روزرسانی شد.")


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.data.startswith("admin_rates_"):
        await rate_management_callback(update, context)
        return
    await query.answer()

    if query.data in {"main", "account_main"}:
        await query.edit_message_text(
            MAIN_MENU_TEXT,
            reply_markup=build_main_keyboard(),
        )
    elif query.data == "remittance":
        await query.edit_message_text(
            "🌍 حواله و انتقال پول\n\nبرای ثبت یک درخواست آزمایشی گزینه زیر را انتخاب کنید.",
            reply_markup=build_remittance_menu_keyboard(),
        )
    elif query.data == "remittance_send":
        await start_remittance(update, context)
    elif query.data == "buy_sell":
        await query.edit_message_text(
            "💱 خرید و فروش ارز\n\nلطفاً نوع معاملهٔ مورد نظر را انتخاب کنید.",
            reply_markup=build_buy_sell_keyboard(),
        )
    elif query.data in {"buy_currency", "sell_currency"}:
        await query.edit_message_text(
            "⚠️ ثبت یا تأیید معامله در حال حاضر در دسترس نیست.\n"
            "هیچ معامله‌ای ثبت یا انجام نشده است.",
            reply_markup=build_buy_sell_keyboard(),
        )
    elif query.data == "account":
        await show_account(update, context)
    elif query.data == "account_remittances":
        await show_customer_remittances(update, context)
    elif query.data == "account_active":
        await show_customer_remittances(update, context, active_only=True)
    elif query.data == "account_history":
        await show_customer_history(update, context)
    elif query.data == "account_track":
        context.user_data["account_tracking"] = True
        await query.edit_message_text(
            "🔎 کد پیگیری حواله را وارد کنید:\nمثال: LA-20260923-1001",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ بازگشت", callback_data="account")]]
            ),
        )
    elif query.data == "account_support":
        context.user_data["account_support"] = True
        await query.edit_message_text(
            "💬 پیام خود را برای پشتیبانی ارسال کنید.\n"
            "پیام شما به حواله فعال مرتبط می‌شود.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ بازگشت", callback_data="account")]]
            ),
        )
    elif query.data == "account_security":
        await query.edit_message_text(
            "🔐 اطلاعات حساب شما محرمانه است و به کاربران دیگر نمایش داده نمی‌شود.\n"
            "از اطلاعات شما فقط برای ارائه خدمات استفاده می‌شود.",
            reply_markup=build_account_keyboard(),
        )
    elif query.data == "remit_cancel":
        get_remittance_store().delete_draft(query.from_user.id)
        clear_remittance(context)
        await query.edit_message_text("❌ درخواست حواله لغو شد.", reply_markup=build_main_keyboard())
    elif query.data.startswith("remit_currency_"):
        request = context.user_data.get("remittance")
        if not request:
            request = get_remittance_store().get_draft(query.from_user.id)
        if request and request.get("step") == "currency":
            updated_request = dict(request)
            updated_request["currency"] = query.data.rsplit("_", 1)[1]
            updated_request["step"] = "city"
            get_remittance_store().save_draft(query.from_user.id, updated_request)
            context.user_data["remittance"] = updated_request
            await query.edit_message_text("📍 شهر دریافت در افغانستان را انتخاب کنید:", reply_markup=build_remittance_cities_keyboard())
    elif query.data.startswith("remit_city_"):
        request = context.user_data.get("remittance")
        if not request:
            request = get_remittance_store().get_draft(query.from_user.id)
        cities = ("کابل", "هرات", "مزار شریف", "قندهار", "جلال‌آباد", "سایر")
        if request and request.get("step") == "city":
            updated_request = dict(request)
            updated_request["city"] = cities[int(query.data.rsplit("_", 1)[1])]
            updated_request["step"] = "document"
            get_remittance_store().save_draft(query.from_user.id, updated_request)
            context.user_data["remittance"] = updated_request
            await query.edit_message_text("🪪 عکس تذکره یا پاسپورت گیرنده را ارسال کنید:", reply_markup=build_back_keyboard("remit_cancel"))
    elif query.data == "remit_confirm":
        await confirm_remittance(update, context)
    elif query.data.startswith("admin_"):
        await admin_remittance_callback(update, context)
    elif query.data == "transactions":
        await query.edit_message_text(
            "🚧 این بخش به‌زودی فعال خواهد شد.\n\nبرای نسخه آینده، این گزینه به workflow اختصاصی SARWARI EXCHANGE متصل خواهد شد.",
            reply_markup=build_back_keyboard(),
        )
    elif query.data == "support":
        await query.edit_message_text(
            "🆘 پشتیبانی SARWARI EXCHANGE\n\n"
            "برای دریافت راهنمایی، استعلام نرخ، پیگیری خدمات و ارتباط با صرافی، از طریق واتساپ با ما در تماس باشید.\n\n"
            "🤝 پشتیبانی و پاسخگویی\n"
            "📱 واتساپ: https://wa.me/message/FCRIX7IXIPU7I1",
            reply_markup=build_support_keyboard(),
        )
    elif query.data == "rates":
        await show_rates(update, context, edit=True)
    elif query.data == "refresh":
        try:
            await get_managed_rates().refresh_reference(force=True)
            await show_rates(update, context, edit=True)
        except (requests.RequestException, ValueError, TypeError, KeyError, sqlite3.Error) as error:
            logging.warning("Currency refresh failed: %s", error)
            await query.edit_message_text(RATE_ERROR, reply_markup=build_back_keyboard("refresh"))
    elif query.data == "refresh_gold":
        try:
            await get_managed_rates().refresh_gold(force=True)
            await show_gold(update, context, edit=True)
        except (requests.RequestException, ValueError, TypeError, KeyError, sqlite3.Error) as error:
            logging.warning("Gold refresh failed: %s", error)
            await query.edit_message_text(GOLD_ERROR, reply_markup=build_back_keyboard("refresh_gold"))
    elif query.data == "compare":
        await show_comparison(update, context, edit=True)
    elif query.data == "gold":
        await show_gold(update, context, edit=True)
    elif query.data == "news":
        await show_news(update, context, edit=True)
    elif query.data == "help":
        await query.edit_message_text(
            format_about_message(),
            reply_markup=build_back_keyboard(),
        )
    elif query.data == "about":
        await query.edit_message_text(
            format_about_message(),
            reply_markup=build_back_keyboard(),
        )
def main():
    instance_lock = acquire_instance_lock()
    try:
        global REMITTANCE_STORE, MANAGED_RATES
        REMITTANCE_STORE = RemittanceStore(SETTINGS.database_path)
        REMITTANCE_STORE.initialize()
        MANAGED_RATES = ManagedRatesService(REMITTANCE_STORE, RATES_SERVICE)
        loaded_remittances = REMITTANCE_STORE.load_remittances()
        logging.info("Loaded %d persisted remittances", len(loaded_remittances))

        app = (
            Application.builder()
            .token(BOT_TOKEN)
            .post_init(start_rate_refresh_task)
            .post_stop(stop_rate_refresh_task)
            .build()
        )
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("myid", myid))
        app.add_handler(CommandHandler("help", help_command))
        app.add_handler(CommandHandler("adminrates", admin_rates_command))
        app.add_handler(CommandHandler("rates", rates))
        app.add_handler(CommandHandler("gold", gold))
        app.add_handler(CommandHandler("news", news))
        app.add_handler(CallbackQueryHandler(button_callback))
        app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, web_app_callback))
        app.add_handler(
            MessageHandler(filters.PHOTO | (filters.TEXT & ~filters.COMMAND), remittance_message)
        )
        app.add_error_handler(handle_application_error)

        logging.info("Bot is running")
        app.run_polling()
    finally:
        fcntl.flock(instance_lock, fcntl.LOCK_UN)
        instance_lock.close()


async def start_rate_refresh_task(application: Application) -> None:
    async def refresh_loop() -> None:
        while True:
            try:
                await get_managed_rates().refresh_all()
            except (sqlite3.Error, requests.RequestException, ValueError) as error:
                logging.warning("Automatic rate refresh failed: %s", error)
            await asyncio.sleep(60)

    application.bot_data["rate_refresh_task"] = asyncio.create_task(refresh_loop())


async def stop_rate_refresh_task(application: Application) -> None:
    task = application.bot_data.pop("rate_refresh_task", None)
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def handle_application_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    error = context.error
    if not isinstance(error, sqlite3.Error):
        logging.error(
            "Unhandled bot error",
            exc_info=(type(error), error, error.__traceback__) if error else None,
        )
        return

    logging.error(
        "Bot could not access persistent remittance storage",
        exc_info=(type(error), error, error.__traceback__),
    )
    message = "⚠️ ذخیره یا دریافت اطلاعات حواله فعلاً ممکن نیست. لطفاً دوباره تلاش کنید."
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text(message)


if __name__ == "__main__":
    main()