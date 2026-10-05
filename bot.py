import asyncio
import fcntl
import json
import logging
import os
import re
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
from models import ProviderResult
from services.rates import RatesService
from utils.formatting import format_comparison, format_gold, format_reference_rates, format_xe_rates

BOT_TOKEN = os.getenv("BOT_TOKEN")
REQUEST_TIMEOUT = 15
INSTANCE_LOCK_PATH = "/tmp/telegram-bot.lock"
GRAMS_PER_TROY_OUNCE = 31.1034768
RATE_ERROR = "âš ï¸ ÙØ¹Ù„Ø§Ù‹ Ø¯Ø±ÛŒØ§ÙØª Ù†Ø±Ø®â€ŒÙ‡Ø§ Ù…Ù…Ú©Ù† Ù†ÛŒØ³Øª. Ù„Ø·ÙØ§Ù‹ Ú†Ù†Ø¯ Ù„Ø­Ø¸Ù‡ Ø¨Ø¹Ø¯ Ø¯ÙˆØ¨Ø§Ø±Ù‡ ØªÙ„Ø§Ø´ Ú©Ù†ÛŒØ¯."
GOLD_ERROR = "âš ï¸ Ø§Ø·Ù„Ø§Ø¹Ø§Øª Ù†Ø±Ø® Ø·Ù„Ø§ Ø¯Ø± Ø¯Ø³ØªØ±Ø³ Ù†ÛŒØ³Øª. Ù„Ø·ÙØ§Ù‹ Ø¨Ø¹Ø¯Ø§Ù‹ Ø¯ÙˆØ¨Ø§Ø±Ù‡ ØªÙ„Ø§Ø´ Ú©Ù†ÛŒØ¯."
MAIN_MENU_TEXT = (
    "ðŸ¦ SARWARI EXCHANGE\n\n"
    "ØµØ±Ø§ÙÛŒ Ùˆ Ø®Ø¯Ù…Ø§Øª Ù¾ÙˆÙ„ÛŒ Ø³Ø±ÙˆØ±ÛŒ\n\n"
    "âœ¨ Ø§Ø¹ØªÙ…Ø§Ø¯ â€¢ Ø´ÙØ§ÙÛŒØª â€¢ Ø³Ø±Ø¹Øª\n"
    "â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”\n\n"
    "ðŸŒ¿ Ø¨Ù‡ ØµØ±Ø§ÙÛŒ Ø³Ø±ÙˆØ±ÛŒ Ø®ÙˆØ´ Ø¢Ù…Ø¯ÛŒØ¯!\n\n"
    "Ø®Ø¯Ù…Ø§Øª Ù…Ø§Ù„ÛŒ Ø´Ù…Ø§ØŒ Ø³Ø§Ø¯Ù‡ Ùˆ Ø¯Ø± Ø¯Ø³ØªØ±Ø³.\n\n"
    "Ù„Ø·ÙØ§Ù‹ Ø®Ø¯Ù…Øª Ù…ÙˆØ±Ø¯ Ù†Ø¸Ø± Ø®ÙˆØ¯ Ø±Ø§ Ø§Ù†ØªØ®Ø§Ø¨ Ú©Ù†ÛŒØ¯:"
)
REMITS_COMMISSION_RATE = 0.04
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
WEB_APP_URL = os.getenv("WEB_APP_URL")
REMITS_COUNTER = 1000
REMITS: dict[str, dict] = {}
CUSTOMER_CODES: dict[int, str] = {}
CUSTOMER_CODE_COUNTER = 10024
REMITTANCE_STATUSES = (
    "ðŸ• Ø¯Ø± Ø§Ù†ØªØ¸Ø§Ø± Ø¨Ø±Ø±Ø³ÛŒ",
    "âœ… ØªØ£ÛŒÛŒØ¯ Ø´Ø¯",
    "âš™ï¸ Ø¯Ø± Ø­Ø§Ù„ Ù¾Ø±Ø¯Ø§Ø²Ø´",
    "ðŸ’° Ø¢Ù…Ø§Ø¯Ù‡ Ø¯Ø±ÛŒØ§ÙØª",
    "âœ… Ù¾Ø±Ø¯Ø§Ø®Øª Ø´Ø¯",
    "âŒ Ø±Ø¯ Ø´Ø¯",
    "ðŸš« Ù„ØºÙˆ Ø´Ø¯",
)
REMITTANCE_STATUS_BY_KEY = {
    "processing": "âš™ï¸ Ø¯Ø± Ø­Ø§Ù„ Ù¾Ø±Ø¯Ø§Ø²Ø´",
    "ready": "ðŸ’° Ø¢Ù…Ø§Ø¯Ù‡ Ø¯Ø±ÛŒØ§ÙØª",
    "paid": "âœ… Ù¾Ø±Ø¯Ø§Ø®Øª Ø´Ø¯",
    "cancelled": "ðŸš« Ù„ØºÙˆ Ø´Ø¯",
}
ACTIVE_REMITTANCE_STATUSES = {
    "ðŸ• Ø¯Ø± Ø§Ù†ØªØ¸Ø§Ø± Ø¨Ø±Ø±Ø³ÛŒ",
    "âœ… ØªØ£ÛŒÛŒØ¯ Ø´Ø¯",
    "âš™ï¸ Ø¯Ø± Ø­Ø§Ù„ Ù¾Ø±Ø¯Ø§Ø²Ø´",
    "ðŸ’° Ø¢Ù…Ø§Ø¯Ù‡ Ø¯Ø±ÛŒØ§ÙØª",
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
            InlineKeyboardButton("ðŸ’± Ø®Ø±ÛŒØ¯ Ùˆ ÙØ±ÙˆØ´ Ø§Ø±Ø²", callback_data="buy_sell"),
            InlineKeyboardButton("ðŸŒ Ø­ÙˆØ§Ù„Ù‡ Ùˆ Ø§Ù†ØªÙ‚Ø§Ù„ Ù¾ÙˆÙ„", callback_data="remittance"),
        ],
        [
            InlineKeyboardButton("ðŸ“Š Ù†Ø±Ø® Ø§Ø³Ø¹Ø§Ø±", callback_data="rates"),
            InlineKeyboardButton("ðŸ¥‡ Ù†Ø±Ø® Ø·Ù„Ø§", callback_data="gold"),
        ],
        [
            InlineKeyboardButton("ðŸ§¾ Ù…Ø¹Ø§Ù…Ù„Ø§Øª Ù…Ù†", callback_data="transactions"),
            InlineKeyboardButton("ðŸ‘¤ Ø­Ø³Ø§Ø¨ Ù…Ù†", callback_data="account"),
        ],
        [
            InlineKeyboardButton("ðŸ†˜ Ù¾Ø´ØªÛŒØ¨Ø§Ù†ÛŒ", callback_data="support"),
            InlineKeyboardButton("â„¹ï¸ Ø¯Ø±Ø¨Ø§Ø±Ù‡ ØµØ±Ø§ÙÛŒ", callback_data="about"),
        ],
    ]
    return InlineKeyboardMarkup(rows)


def build_buy_sell_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("ðŸŸ¢ Ø®Ø±ÛŒØ¯ Ø§Ø±Ø²", callback_data="buy_currency"),
                InlineKeyboardButton("ðŸ”´ ÙØ±ÙˆØ´ Ø§Ø±Ø²", callback_data="sell_currency"),
            ],
            [InlineKeyboardButton("â†©ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª", callback_data="main")],
        ]
    )


def build_webapp_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton("âœ¨ Ù…Ù†ÙˆÛŒ Ù…Ø¯Ø±Ù†", web_app=WebAppInfo(url=WEB_APP_URL))]],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def build_back_keyboard(refresh_action: str | None = None) -> InlineKeyboardMarkup:
    buttons = []
    if refresh_action:
        buttons.append(InlineKeyboardButton("ðŸ”„ Ø¨Ø±ÙˆØ²Ø±Ø³Ø§Ù†ÛŒ", callback_data=refresh_action))
    buttons.append(InlineKeyboardButton("â†©ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª", callback_data="main"))
    return InlineKeyboardMarkup([buttons])


def build_support_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "ðŸ“² ØªÙ…Ø§Ø³ Ø¨Ø§ Ù¾Ø´ØªÛŒØ¨Ø§Ù†ÛŒ Ø¯Ø± ÙˆØ§ØªØ³Ø§Ù¾",
                    url="https://wa.me/message/FCRIX7IXIPU7I1",
                )
            ],
            [InlineKeyboardButton("ðŸ”™ Ø¨Ø§Ø²Ú¯Ø´Øª Ø¨Ù‡ Ù…Ù†ÙˆÛŒ Ø§ØµÙ„ÛŒ", callback_data="main")],
        ]
    )


def build_remittance_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("ðŸ’¸ Ø§Ø±Ø³Ø§Ù„ Ø­ÙˆØ§Ù„Ù‡", callback_data="remittance_send")],
            [InlineKeyboardButton("â†©ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª", callback_data="main")],
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
            [InlineKeyboardButton("âŒ Ù„ØºÙˆ Ø¯Ø±Ø®ÙˆØ§Ø³Øª", callback_data="remit_cancel")],
        ]
    )


def build_remittance_cities_keyboard() -> InlineKeyboardMarkup:
    cities = ("Ú©Ø§Ø¨Ù„", "Ù‡Ø±Ø§Øª", "Ù…Ø²Ø§Ø± Ø´Ø±ÛŒÙ", "Ù‚Ù†Ø¯Ù‡Ø§Ø±", "Ø¬Ù„Ø§Ù„â€ŒØ¢Ø¨Ø§Ø¯", "Ø³Ø§ÛŒØ±")
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(city, callback_data=f"remit_city_{index}")]
            for index, city in enumerate(cities)
        ]
        + [[InlineKeyboardButton("âŒ Ù„ØºÙˆ Ø¯Ø±Ø®ÙˆØ§Ø³Øª", callback_data="remit_cancel")]]
    )


def build_remittance_summary_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("âœ… ØªØ£ÛŒÛŒØ¯ Ùˆ Ø§Ø±Ø³Ø§Ù„ Ø¨Ù‡ Ù…Ø¯ÛŒØ±ÛŒØª", callback_data="remit_confirm")],
            [InlineKeyboardButton("âœï¸ Ø§ØµÙ„Ø§Ø­ Ø§Ø·Ù„Ø§Ø¹Ø§Øª", callback_data="remittance_send")],
            [InlineKeyboardButton("âŒ Ù„ØºÙˆ Ø¯Ø±Ø®ÙˆØ§Ø³Øª", callback_data="remit_cancel")],
        ]
    )


def build_account_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("ðŸ“‹ Ø­ÙˆØ§Ù„Ù‡â€ŒÙ‡Ø§ÛŒ Ù…Ù†", callback_data="account_remittances"),
                InlineKeyboardButton("ðŸ”Ž Ù¾ÛŒÚ¯ÛŒØ±ÛŒ Ø­ÙˆØ§Ù„Ù‡", callback_data="account_track"),
            ],
            [
                InlineKeyboardButton("ðŸ’° Ø­ÙˆØ§Ù„Ù‡ ÙØ¹Ø§Ù„", callback_data="account_active"),
                InlineKeyboardButton("ðŸ§¾ Ø³ÙˆØ§Ø¨Ù‚ Ù…Ø¹Ø§Ù…Ù„Ø§Øª", callback_data="account_history"),
            ],
            [
                InlineKeyboardButton("ðŸ’¬ Ù¾Ø´ØªÛŒØ¨Ø§Ù†ÛŒ", callback_data="account_support"),
                InlineKeyboardButton("ðŸ” Ø§Ù…Ù†ÛŒØª Ø­Ø³Ø§Ø¨", callback_data="account_security"),
            ],
            [InlineKeyboardButton("â¬…ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª", callback_data="account_main")],
        ]
    )


def calculate_remittance_totals(amount: float) -> tuple[float, float]:
    commission = round(amount * REMITS_COMMISSION_RATE, 2)
    return commission, round(amount + commission, 2)


def get_customer_code(customer_chat_id: int) -> str:
    global CUSTOMER_CODE_COUNTER
    if customer_chat_id not in CUSTOMER_CODES:
        CUSTOMER_CODE_COUNTER += 1
        CUSTOMER_CODES[customer_chat_id] = f"CUS-{CUSTOMER_CODE_COUNTER}"
    return CUSTOMER_CODES[customer_chat_id]


def get_customer_remittances(customer_chat_id: int) -> list[dict]:
    return [
        request
        for request in REMITS.values()
        if request.get("customer_chat_id") == customer_chat_id
    ]


def format_account_panel(customer_name: str, customer_chat_id: int) -> str:
    requests = get_customer_remittances(customer_chat_id)
    pending_count = sum(request.get("status") == "ðŸ• Ø¯Ø± Ø§Ù†ØªØ¸Ø§Ø± Ø¨Ø±Ø±Ø³ÛŒ" for request in requests)
    processing_count = sum(request.get("status") == "âš™ï¸ Ø¯Ø± Ø­Ø§Ù„ Ù¾Ø±Ø¯Ø§Ø²Ø´" for request in requests)
    paid_count = sum(request.get("status") == "âœ… Ù¾Ø±Ø¯Ø§Ø®Øª Ø´Ø¯" for request in requests)
    return (
        "ðŸ‘¤ Ø­Ø³Ø§Ø¨ Ú©Ø§Ø±Ø¨Ø±ÛŒ Ù…Ù†\n"
        "â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”\n"
        f"Ø³Ù„Ø§Ù…ØŒ {customer_name} Ø¹Ø²ÛŒØ² ðŸ‘‹\n"
        f"ðŸ†” Ú©Ø¯ Ù…Ø´ØªØ±ÛŒ: {get_customer_code(customer_chat_id)}\n"
        "ðŸŸ¢ ÙˆØ¶Ø¹ÛŒØª Ø­Ø³Ø§Ø¨: ÙØ¹Ø§Ù„\n\n"
        "ðŸ’¸ Ø®Ù„Ø§ØµÙ‡ ÙØ¹Ø§Ù„ÛŒØª\n"
        f"ðŸ“‹ Ú©Ù„ Ø­ÙˆØ§Ù„Ù‡â€ŒÙ‡Ø§: {len(requests)}\n"
        f"ðŸ• Ø¯Ø± Ø­Ø§Ù„ Ø¨Ø±Ø±Ø³ÛŒ: {pending_count}\n"
        f"âš™ï¸ Ø¯Ø± Ø­Ø§Ù„ Ù¾Ø±Ø¯Ø§Ø²Ø´: {processing_count}\n"
        f"âœ… Ù¾Ø±Ø¯Ø§Ø®Øªâ€ŒØ´Ø¯Ù‡: {paid_count}\n"
        "â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”\n"
        "ðŸ” Ø§Ø·Ù„Ø§Ø¹Ø§Øª Ø´Ù…Ø§ Ù…Ø­Ø±Ù…Ø§Ù†Ù‡ Ø§Ø³Øª."
    )


def format_customer_remittance(request: dict) -> str:
    return (
        f"ðŸ†” Ú©Ø¯ Ù¾ÛŒÚ¯ÛŒØ±ÛŒ: {request['tracking_code']}\n"
        f"ðŸ’° Ù…Ø¨Ù„Øº: {request['amount']:g} {request['currency']}\n"
        f"ðŸ“ Ø´Ù‡Ø±: {request['city']}\n"
        f"ðŸ• ÙˆØ¶Ø¹ÛŒØª: {request.get('status', 'ðŸ• Ø¯Ø± Ø§Ù†ØªØ¸Ø§Ø± Ø¨Ø±Ø±Ø³ÛŒ')}"
    )


def format_remittance_summary(request: dict) -> str:
    commission, total = calculate_remittance_totals(request["amount"])
    return (
        "ðŸ§¾ Ø®Ù„Ø§ØµÙ‡ Ø¯Ø±Ø®ÙˆØ§Ø³Øª Ø­ÙˆØ§Ù„Ù‡\n\n"
        f"ðŸ‘¤ ÙØ±Ø³ØªÙ†Ø¯Ù‡: {request['sender']}\n"
        f"ðŸ‘¤ Ú¯ÛŒØ±Ù†Ø¯Ù‡: {request['recipient']}\n"
        f"ðŸ’° Ù…Ø¨Ù„Øº: {request['amount']:g}\n"
        f"ðŸ’± ÙˆØ§Ø­Ø¯ Ù¾ÙˆÙ„: {request['currency']}\n"
        f"ðŸ“ Ù…Ø­Ù„ Ø¯Ø±ÛŒØ§ÙØª: {request['city']}\n"
        f"ðŸ’³ Ú©Ù…ÛŒØ´Ù†: 4Ùª = {commission:g} {request['currency']}\n"
        f"ðŸ’µ Ù…Ø¬Ù…ÙˆØ¹: {total:g} {request['currency']}\n"
        "ðŸªª Ù…Ø¯Ø±Ú© Ú¯ÛŒØ±Ù†Ø¯Ù‡: âœ… Ø¯Ø±ÛŒØ§ÙØª Ø´Ø¯"
    )


def format_admin_remittance(request: dict) -> str:
    commission, total = calculate_remittance_totals(request["amount"])
    return (
        "ðŸ”” Ø¯Ø±Ø®ÙˆØ§Ø³Øª Ø¬Ø¯ÛŒØ¯ Ø­ÙˆØ§Ù„Ù‡\n\n"
        f"ðŸ†” Ú©Ø¯ Ù¾ÛŒÚ¯ÛŒØ±ÛŒ: {request['tracking_code']}\n"
        f"ðŸ‘¤ ÙØ±Ø³ØªÙ†Ø¯Ù‡: {request['sender']}\n"
        f"ðŸ‘¤ Ú¯ÛŒØ±Ù†Ø¯Ù‡: {request['recipient']}\n"
        f"ðŸ’° Ù…Ø¨Ù„Øº: {request['amount']:g}\n"
        f"ðŸ’± ÙˆØ§Ø­Ø¯ Ù¾ÙˆÙ„: {request['currency']}\n"
        f"ðŸ“ Ø´Ù‡Ø±: {request['city']}\n"
        f"ðŸ’³ Ú©Ù…ÛŒØ´Ù†: 4Ùª = {commission:g} {request['currency']}\n"
        f"ðŸ’µ Ù…Ø¬Ù…ÙˆØ¹: {total:g} {request['currency']}\n"
        "ðŸªª Ù…Ø¯Ø±Ú© Ú¯ÛŒØ±Ù†Ø¯Ù‡: Ù¾ÛŒÙˆØ³Øª Ø´Ø¯Ù‡\n"
        f"ðŸ• ÙˆØ¶Ø¹ÛŒØª: {request.get('status', 'ðŸ• Ø¯Ø± Ø§Ù†ØªØ¸Ø§Ø± Ø¨Ø±Ø±Ø³ÛŒ')}"
    )


def build_admin_remittance_keyboard(tracking_code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("âœ… ØªØ£ÛŒÛŒØ¯ Ø­ÙˆØ§Ù„Ù‡", callback_data=f"admin_approve_{tracking_code}"),
                InlineKeyboardButton("âŒ Ø±Ø¯ Ø­ÙˆØ§Ù„Ù‡", callback_data=f"admin_reject_{tracking_code}"),
            ],
            [InlineKeyboardButton("ðŸ’¬ Ø¯Ø±Ø®ÙˆØ§Ø³Øª Ø§Ø·Ù„Ø§Ø¹Ø§Øª Ø¨ÛŒØ´ØªØ±", callback_data=f"admin_more_{tracking_code}")],
        ]
    )


def build_admin_status_keyboard(tracking_code: str, status: str) -> InlineKeyboardMarkup:
    next_buttons = {
        "âœ… ØªØ£ÛŒÛŒØ¯ Ø´Ø¯": [
            InlineKeyboardButton("âš™ï¸ Ø¯Ø± Ø­Ø§Ù„ Ù¾Ø±Ø¯Ø§Ø²Ø´", callback_data=f"admin_status_processing_{tracking_code}"),
            InlineKeyboardButton("ðŸš« Ù„ØºÙˆ Ø­ÙˆØ§Ù„Ù‡", callback_data=f"admin_status_cancelled_{tracking_code}"),
        ],
        "âš™ï¸ Ø¯Ø± Ø­Ø§Ù„ Ù¾Ø±Ø¯Ø§Ø²Ø´": [
            InlineKeyboardButton("ðŸ’° Ø¢Ù…Ø§Ø¯Ù‡ Ø¯Ø±ÛŒØ§ÙØª", callback_data=f"admin_status_ready_{tracking_code}"),
            InlineKeyboardButton("ðŸš« Ù„ØºÙˆ Ø­ÙˆØ§Ù„Ù‡", callback_data=f"admin_status_cancelled_{tracking_code}"),
        ],
        "ðŸ’° Ø¢Ù…Ø§Ø¯Ù‡ Ø¯Ø±ÛŒØ§ÙØª": [
            InlineKeyboardButton("âœ… Ù¾Ø±Ø¯Ø§Ø®Øª Ø´Ø¯", callback_data=f"admin_status_paid_{tracking_code}"),
            InlineKeyboardButton("ðŸš« Ù„ØºÙˆ Ø­ÙˆØ§Ù„Ù‡", callback_data=f"admin_status_cancelled_{tracking_code}"),
        ],
    }
    rows = [next_buttons[status]] if status in next_buttons else []
    rows.append([InlineKeyboardButton("ðŸ’¬ Ù¾ÛŒØ§Ù… Ø¨Ù‡ Ù…Ø´ØªØ±ÛŒ", callback_data=f"admin_message_{tracking_code}")])
    return InlineKeyboardMarkup(rows)


def is_admin_user(user_id: int | None) -> bool:
    return bool(ADMIN_CHAT_ID) and str(user_id) == ADMIN_CHAT_ID


def get_active_customer_remittances(customer_chat_id: int) -> list[dict]:
    return [
        request
        for request in REMITS.values()
        if request.get("customer_chat_id") == customer_chat_id
        and request.get("status") in ACTIVE_REMITTANCE_STATUSES
    ]


def extract_tracking_code(text: str) -> str | None:
    match = TRACKING_CODE_PATTERN.search(text)
    return match.group(0) if match else None


def set_remittance_status(tracking_code: str, status_key: str) -> dict | None:
    request = REMITS.get(tracking_code)
    status = REMITTANCE_STATUS_BY_KEY.get(status_key)
    if not request or not status:
        return None
    request["status"] = status
    return request


def clear_remittance(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("remittance", None)


def format_about_message() -> str:
    return (
        "ðŸ¦ ØµØ±Ø§ÙÛŒ Ùˆ Ø®Ø¯Ù…Ø§Øª Ù¾ÙˆÙ„ÛŒ Ø³Ø±ÙˆØ±ÛŒ\n"
        "SARWARI EXCHANGE\n"
        "â”â”â”â”â”â”â”â”â”â”â”â”â”\n"
        "ðŸ” Ø§Ù…Ù†ÛŒØª | âš¡ Ø³Ø±Ø¹Øª | ðŸ¤ Ø§Ø¹ØªÙ…Ø§Ø¯\n\n"
        "Ù…Ø§ Ø¯Ø± Ø²Ù…ÛŒÙ†Ù‡ Ø®Ø¯Ù…Ø§Øª ØµØ±Ø§ÙÛŒ Ùˆ Ø§Ù†ØªÙ‚Ø§Ù„ Ù¾ÙˆÙ„ ÙØ¹Ø§Ù„ÛŒØª Ù…ÛŒâ€ŒÚ©Ù†ÛŒÙ… Ùˆ ØªÙ„Ø§Ø´ Ø¯Ø§Ø±ÛŒÙ… Ø®Ø¯Ù…Ø§Øª Ø³Ø±ÛŒØ¹ØŒ Ø´ÙØ§Ù Ùˆ Ù‚Ø§Ø¨Ù„ Ø§Ø¹ØªÙ…Ø§Ø¯ Ø±Ø§ Ø¨Ø±Ø§ÛŒ Ù…Ø´ØªØ±ÛŒØ§Ù† Ø§Ø±Ø§Ø¦Ù‡ Ú©Ù†ÛŒÙ….\n\n"
        "ðŸ“ Ø¢Ø¯Ø±Ø³:\n"
        "Ú©Ø§Ø¨Ù„ØŒ Ú©ÙˆØªÙ‡â€ŒØ³Ù†Ú¯ÛŒØŒ Ù…Ø§Ø±Ú©ÛŒØª Ø¢Ø±ÛŒØ§Ù†Ø§ Ú©Ø§Ø¨Ù„ØŒ\n"
        "Ù…Ù†Ø²Ù„ Ø§ÙˆÙ„ØŒ Ø¯Ú©Ø§Ù† Û¶Û´\n\n"
        "ðŸ“ž Ø¨Ø±Ø§ÛŒ Ø¯Ø±ÛŒØ§ÙØª Ù†Ø±Ø® Ùˆ Ø®Ø¯Ù…Ø§Øª Ø¨ÛŒØ´ØªØ± Ø¨Ø§ Ù¾Ø´ØªÛŒØ¨Ø§Ù†ÛŒ ØªÙ…Ø§Ø³ Ø¨Ú¯ÛŒØ±ÛŒØ¯."
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
            "âœ¨ Ø¨Ø±Ø§ÛŒ Ø¨Ø§Ø² Ú©Ø±Ø¯Ù† Ù…Ù†ÙˆÛŒ Ù…Ø¯Ø±Ù†ØŒ Ø¯Ú©Ù…Ù‡ Ø²ÛŒØ± Ø±Ø§ Ø¨Ø²Ù†ÛŒØ¯.",
            reply_markup=build_webapp_keyboard(),
        )


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat and update.effective_chat.type == "private":
        await update.message.reply_text(str(update.effective_user.id))


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "â„¹ï¸ Ø¯Ø±Ø¨Ø§Ø±Ù‡ Ø±Ø¨Ø§Øª\n"
        "â”â”â”â”â”â”â”â”â”â”â”â”â”\n"
        "/start - Ù…Ù†ÙˆÛŒ Ø§ØµÙ„ÛŒ\n"
        "/rates - Ù†Ø±Ø® Ø¯Ø§Ù„Ø±ØŒ ÛŒÙˆØ±Ùˆ Ùˆ Ø§Ø±Ø²Ù‡Ø§ÛŒ Ù…Ù†Ø·Ù‚Ù‡\n"
        "/gold - Ù†Ø±Ø® ØªÙ‚Ø±ÛŒØ¨ÛŒ Ø·Ù„Ø§ Ø¨Ù‡ Ø§Ø²Ø§ÛŒ Ù‡Ø± Ú¯Ø±Ø§Ù…\n"
        "/news - Ø¢Ø®Ø±ÛŒÙ† Ø§Ø®Ø¨Ø§Ø± Ù…Ù‡Ù… Ø¨ÛŒÙ†â€ŒØ§Ù„Ù…Ù„Ù„ÛŒ\n"
        "/help - Ø±Ø§Ù‡Ù†Ù…Ø§\n"
        "/news - Ø§Ø®Ø¨Ø§Ø± Ø±ÙˆØ²"
    )
    await update.message.reply_text(text, reply_markup=build_back_keyboard())


async def show_rates(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    edit: bool = False,
    reference_result: ProviderResult | None = None,
):
    try:
        reference = reference_result or await RATES_SERVICE.get_reference_rates()
        text = format_reference_rates(reference.rates) if reference.rates else (reference.message or RATE_ERROR)
    except (requests.RequestException, ValueError, TypeError, KeyError) as error:
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


async def show_comparison(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False):
    try:
        reference_result, xe_result, dab_result = await asyncio.gather(
            RATES_SERVICE.get_reference_rates(),
            RATES_SERVICE.get_xe(),
            RATES_SERVICE.get_dab(),
        )
        text = format_comparison(
            reference_result.rates,
            xe_result.rates,
            dab_result.available,
        )
    except (requests.RequestException, ValueError, TypeError, KeyError, ZeroDivisionError) as error:
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
    gold_result: ProviderResult | None = None,
):
    try:
        result = gold_result or await RATES_SERVICE.get_gold()
        text = format_gold(result.gold) if result.gold else GOLD_ERROR
    except (requests.RequestException, ValueError, TypeError, KeyError) as error:
        logging.warning("Gold API failed: %s", error)
        text = GOLD_ERROR

    keyboard = build_back_keyboard("refresh_gold")
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
        text = "âš ï¸ ÙØ¹Ù„Ø§Ù‹ Ø¯Ø±ÛŒØ§ÙØª Ø§Ø®Ø¨Ø§Ø± Ù…Ù…Ú©Ù† Ù†ÛŒØ³Øª. Ù„Ø·ÙØ§Ù‹ Ú†Ù†Ø¯ Ù„Ø­Ø¸Ù‡ Ø¨Ø¹Ø¯ Ø¯ÙˆØ¨Ø§Ø±Ù‡ ØªÙ„Ø§Ø´ Ú©Ù†ÛŒØ¯."
    else:
        text = "ðŸ“° Ø§Ø®Ø¨Ø§Ø± Ù…Ù‡Ù… Ø¨ÛŒÙ†â€ŒØ§Ù„Ù…Ù„Ù„ÛŒ\n\n"
        for index, (source, title, link) in enumerate(items, 1):
            text += f"{index}. {title}\nÙ…Ù†Ø¨Ø¹: {source}\n{link}\n\n"

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
        await update.effective_message.reply_text("âš ï¸ Ø¯Ø±Ø®ÙˆØ§Ø³Øª Ù…Ù†ÙˆÛŒ Ù…Ø¯Ø±Ù† Ù…Ø¹ØªØ¨Ø± Ù†ÛŒØ³Øª.")
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
            "ðŸŒ Ø­ÙˆØ§Ù„Ù‡ Ùˆ Ø§Ù†ØªÙ‚Ø§Ù„ Ù¾ÙˆÙ„\n\nØ¨Ø±Ø§ÛŒ Ø«Ø¨Øª Ø¯Ø±Ø®ÙˆØ§Ø³Øª Ú¯Ø²ÛŒÙ†Ù‡ Ø²ÛŒØ± Ø±Ø§ Ø§Ù†ØªØ®Ø§Ø¨ Ú©Ù†ÛŒØ¯.",
            reply_markup=build_remittance_menu_keyboard(),
        )
    elif action == "about":
        await update.effective_message.reply_text(
            format_about_message(), reply_markup=build_back_keyboard()
        )
    elif action == "support":
        await update.effective_message.reply_text(
            "ðŸ†˜ Ù¾Ø´ØªÛŒØ¨Ø§Ù†ÛŒ SARWARI EXCHANGE\n\n"
            "Ø¨Ø±Ø§ÛŒ Ø¯Ø±ÛŒØ§ÙØª Ø±Ø§Ù‡Ù†Ù…Ø§ÛŒÛŒØŒ Ø§Ø³ØªØ¹Ù„Ø§Ù… Ù†Ø±Ø®ØŒ Ù¾ÛŒÚ¯ÛŒØ±ÛŒ Ø®Ø¯Ù…Ø§Øª Ùˆ Ø§Ø±ØªØ¨Ø§Ø· Ø¨Ø§ ØµØ±Ø§ÙÛŒØŒ Ø§Ø² Ø·Ø±ÛŒÙ‚ ÙˆØ§ØªØ³Ø§Ù¾ Ø¨Ø§ Ù…Ø§ Ø¯Ø± ØªÙ…Ø§Ø³ Ø¨Ø§Ø´ÛŒØ¯.",
            reply_markup=build_support_keyboard(),
        )
    elif action == "buy_sell":
        await update.effective_message.reply_text(
            "ðŸ’± Ø®Ø±ÛŒØ¯ Ùˆ ÙØ±ÙˆØ´ Ø§Ø±Ø²\n\nÙ„Ø·ÙØ§Ù‹ Ù†ÙˆØ¹ Ù…Ø¹Ø§Ù…Ù„Ù‡Ù” Ù…ÙˆØ±Ø¯ Ù†Ø¸Ø± Ø±Ø§ Ø§Ù†ØªØ®Ø§Ø¨ Ú©Ù†ÛŒØ¯.",
            reply_markup=build_buy_sell_keyboard(),
        )
    elif action == "transactions":
        await update.effective_message.reply_text(
            "ðŸš§ Ø§ÛŒÙ† Ø¨Ø®Ø´ Ø¨Ù‡â€ŒØ²ÙˆØ¯ÛŒ ÙØ¹Ø§Ù„ Ø®ÙˆØ§Ù‡Ø¯ Ø´Ø¯.", reply_markup=build_back_keyboard()
        )
    else:
        await update.effective_message.reply_text("âš ï¸ Ú¯Ø²ÛŒÙ†Ù‡ Ù…Ù†ÙˆÛŒ Ù…Ø¯Ø±Ù† Ø´Ù†Ø§Ø®ØªÙ‡ Ù†Ø´Ø¯.")


async def show_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = format_account_panel(user.full_name, user.id)
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
    title = "ðŸ’° Ø­ÙˆØ§Ù„Ù‡ ÙØ¹Ø§Ù„" if active_only else "ðŸ“‹ Ø­ÙˆØ§Ù„Ù‡â€ŒÙ‡Ø§ÛŒ Ù…Ù†"
    if requests:
        text = f"{title}\nâ”â”â”â”â”â”â”â”â”â”â”â”â”â”â”\n\n" + "\n\n".join(
            format_customer_remittance(request) for request in requests
        )
    else:
        text = f"{title}\nâ”â”â”â”â”â”â”â”â”â”â”â”â”â”â”\n\nâš ï¸ Ù…ÙˆØ±Ø¯ÛŒ Ø¨Ø±Ø§ÛŒ Ù†Ù…Ø§ÛŒØ´ ÙˆØ¬ÙˆØ¯ Ù†Ø¯Ø§Ø±Ø¯."
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
        await update.message.reply_text("âš ï¸ Ú©Ø¯ Ù¾ÛŒÚ¯ÛŒØ±ÛŒ Ù…Ø¹ØªØ¨Ø± Ù†ÛŒØ³Øª. Ù†Ù…ÙˆÙ†Ù‡: LA-20260923-1001")
        return
    requests = get_customer_remittances(update.effective_user.id)
    request = next(
        (item for item in requests if item.get("tracking_code") == tracking_code),
        None,
    )
    if not request:
        await update.message.reply_text("âš ï¸ Ø­ÙˆØ§Ù„Ù‡â€ŒØ§ÛŒ Ø¨Ø§ Ø§ÛŒÙ† Ú©Ø¯ Ù¾ÛŒÚ¯ÛŒØ±ÛŒ Ø¨Ø±Ø§ÛŒ Ø­Ø³Ø§Ø¨ Ø´Ù…Ø§ Ù¾ÛŒØ¯Ø§ Ù†Ø´Ø¯.")
        return
    await update.message.reply_text(format_customer_remittance(request))


async def start_remittance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["remittance"] = {"step": "sender"}
    await update.callback_query.edit_message_text(
        "ðŸ’¸ Ø§Ø±Ø³Ø§Ù„ Ø­ÙˆØ§Ù„Ù‡\n\nðŸ‘¤ Ù†Ø§Ù… ÙØ±Ø³ØªÙ†Ø¯Ù‡ Ø±Ø§ ÙˆØ§Ø±Ø¯ Ú©Ù†ÛŒØ¯:",
        reply_markup=build_back_keyboard("remit_cancel"),
    )


async def handle_customer_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    account_support = context.user_data.pop("account_support", False)
    if not update.message or not update.message.text or not ADMIN_CHAT_ID:
        return

    active_requests = get_active_customer_remittances(update.effective_user.id)
    if not active_requests:
        if account_support:
            await update.message.reply_text("âš ï¸ Ø¨Ø±Ø§ÛŒ Ø§ØªØµØ§Ù„ Ù¾ÛŒØ§Ù… Ù¾Ø´ØªÛŒØ¨Ø§Ù†ÛŒØŒ Ø­ÙˆØ§Ù„Ù‡ ÙØ¹Ø§Ù„ Ù¾ÛŒØ¯Ø§ Ù†Ø´Ø¯.")
        return

    tracking_code = extract_tracking_code(update.message.text)
    if len(active_requests) > 1 and not tracking_code:
        await update.message.reply_text(
            "âš ï¸ Ú†Ù†Ø¯ Ø­ÙˆØ§Ù„Ù‡ ÙØ¹Ø§Ù„ Ø¯Ø§Ø±ÛŒØ¯. Ù„Ø·ÙØ§Ù‹ Ú©Ø¯ Ù¾ÛŒÚ¯ÛŒØ±ÛŒ Ø­ÙˆØ§Ù„Ù‡ Ø±Ø§ Ø¯Ø± Ù¾ÛŒØ§Ù… Ø®ÙˆØ¯ ÙˆØ§Ø±Ø¯ Ú©Ù†ÛŒØ¯."
        )
        return

    if tracking_code:
        matching_requests = [
            request for request in active_requests if request.get("tracking_code") == tracking_code
        ]
        if not matching_requests:
            await update.message.reply_text("âš ï¸ Ú©Ø¯ Ù¾ÛŒÚ¯ÛŒØ±ÛŒ Ù…Ø¹ØªØ¨Ø± ÛŒØ§ ÙØ¹Ø§Ù„ Ù¾ÛŒØ¯Ø§ Ù†Ø´Ø¯.")
            return
        request = matching_requests[0]
    else:
        request = active_requests[0]

    await context.bot.send_message(
        chat_id=int(ADMIN_CHAT_ID),
        text=(
            "ðŸ’¬ Ù¾ÛŒØ§Ù… Ù…Ø´ØªØ±ÛŒ Ø¯Ø±Ø¨Ø§Ø±Ù‡ Ø­ÙˆØ§Ù„Ù‡\n"
            f"ðŸ†” Ú©Ø¯ Ù¾ÛŒÚ¯ÛŒØ±ÛŒ: {request['tracking_code']}\n"
            f"ðŸ‘¤ ÙØ±Ø³ØªÙ†Ø¯Ù‡: {request['sender']}\n\n"
            f"Ù¾ÛŒØ§Ù… Ù…Ø´ØªØ±ÛŒ:\n{update.message.text}"
        ),
    )
    await update.message.reply_text(
        f"âœ… Ù¾ÛŒØ§Ù… Ø´Ù…Ø§ Ø¨Ø±Ø§ÛŒ Ù…Ø¯ÛŒØ±ÛŒØª Ø§Ø±Ø³Ø§Ù„ Ø´Ø¯.\nðŸ†” Ú©Ø¯ Ù¾ÛŒÚ¯ÛŒØ±ÛŒ: {request['tracking_code']}"
    )


async def handle_admin_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    tracking_code = context.user_data.pop("admin_message_tracking_code", None)
    if not tracking_code:
        return False

    if not is_admin_user(update.effective_user.id) or not update.message or not update.message.text:
        return True

    request = REMITS.get(tracking_code)
    if not request:
        await update.message.reply_text("âš ï¸ Ø­ÙˆØ§Ù„Ù‡ Ù¾ÛŒØ¯Ø§ Ù†Ø´Ø¯.")
        return True

    await context.bot.send_message(
        chat_id=request["customer_chat_id"],
        text=(
            "ðŸ’¬ Ù¾ÛŒØ§Ù… Ù…Ø¯ÛŒØ±ÛŒØª\n"
            f"ðŸ†” Ú©Ø¯ Ù¾ÛŒÚ¯ÛŒØ±ÛŒ: {tracking_code}\n\n"
            f"{update.message.text}"
        ),
    )
    await update.message.reply_text("âœ… Ù¾ÛŒØ§Ù… ÙÙ‚Ø· Ø¨Ø±Ø§ÛŒ Ù…Ø´ØªØ±ÛŒ Ù‡Ù…ÛŒÙ† Ø­ÙˆØ§Ù„Ù‡ Ø§Ø±Ø³Ø§Ù„ Ø´Ø¯.")
    return True


async def remittance_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    request = context.user_data.get("remittance")
    if await handle_admin_message(update, context):
        return

    if context.user_data.get("account_tracking"):
        await handle_account_tracking_message(update, context)
        return

    if not request:
        await handle_customer_message(update, context)
        return
    request = context.user_data.get("remittance")
    if not request:
        return

    if request["step"] == "document":
        if not update.message.photo:
            await update.message.reply_text("ðŸªª Ù„Ø·ÙØ§Ù‹ Ø¹Ú©Ø³ ØªØ°Ú©Ø±Ù‡ ÛŒØ§ Ù¾Ø§Ø³Ù¾ÙˆØ±Øª Ú¯ÛŒØ±Ù†Ø¯Ù‡ Ø±Ø§ Ø§Ø±Ø³Ø§Ù„ Ú©Ù†ÛŒØ¯.")
            return
        request["document_file_id"] = update.message.photo[-1].file_id
        request["step"] = "summary"
        await update.message.reply_text(
            format_remittance_summary(request),
            reply_markup=build_remittance_summary_keyboard(),
        )
        return

    if not update.message.text:
        return
    value = update.message.text.strip()
    step = request["step"]
    if not value:
        await update.message.reply_text("âš ï¸ Ø§ÛŒÙ† Ù…Ù‚Ø¯Ø§Ø± Ù†Ù…ÛŒâ€ŒØªÙˆØ§Ù†Ø¯ Ø®Ø§Ù„ÛŒ Ø¨Ø§Ø´Ø¯. Ø¯ÙˆØ¨Ø§Ø±Ù‡ ØªÙ„Ø§Ø´ Ú©Ù†ÛŒØ¯.")
    elif step == "sender":
        request["sender"] = value
        request["step"] = "recipient"
        await update.message.reply_text("ðŸ‘¤ Ù†Ø§Ù… Ú¯ÛŒØ±Ù†Ø¯Ù‡ Ø±Ø§ ÙˆØ§Ø±Ø¯ Ú©Ù†ÛŒØ¯:")
    elif step == "recipient":
        request["recipient"] = value
        request["step"] = "amount"
        await update.message.reply_text("ðŸ’° Ù…Ø¨Ù„Øº Ø­ÙˆØ§Ù„Ù‡ Ø±Ø§ ÙˆØ§Ø±Ø¯ Ú©Ù†ÛŒØ¯ (Ù…Ø«Ù„Ø§Ù‹ 500):")
    elif step == "amount":
        try:
            amount = float(value.replace(",", ""))
            if amount <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("âš ï¸ Ù…Ø¨Ù„Øº Ù…Ø¹ØªØ¨Ø± Ù†ÛŒØ³Øª. ÛŒÚ© Ø¹Ø¯Ø¯ Ù…Ø«Ø¨Øª ÙˆØ§Ø±Ø¯ Ú©Ù†ÛŒØ¯.")
            return
        request["amount"] = amount
        request["step"] = "currency"
        await update.message.reply_text(
            "ðŸ’± ÙˆØ§Ø­Ø¯ Ù¾ÙˆÙ„ Ø±Ø§ Ø§Ù†ØªØ®Ø§Ø¨ Ú©Ù†ÛŒØ¯:", reply_markup=build_remittance_currencies_keyboard()
        )


async def confirm_remittance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global REMITS_COUNTER
    request = context.user_data.get("remittance")
    if not request or request.get("step") != "summary":
        await update.callback_query.answer("Ø¯Ø±Ø®ÙˆØ§Ø³Øª Ú©Ø§Ù…Ù„ Ù†ÛŒØ³Øª.", show_alert=True)
        return

    REMITS_COUNTER += 1
    tracking_code = f"LA-{datetime.now():%Y%m%d}-{REMITS_COUNTER}"
    request["tracking_code"] = tracking_code
    request["status"] = "ðŸ• Ø¯Ø± Ø§Ù†ØªØ¸Ø§Ø± Ø¨Ø±Ø±Ø³ÛŒ"
    request["customer_chat_id"] = update.callback_query.from_user.id
    REMITS[tracking_code] = dict(request)
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
        "âœ… Ø¯Ø±Ø®ÙˆØ§Ø³Øª Ø­ÙˆØ§Ù„Ù‡ Ø«Ø¨Øª Ø´Ø¯.\n\n"
        f"ðŸ†” Ú©Ø¯ Ù¾ÛŒÚ¯ÛŒØ±ÛŒ: {tracking_code}\n"
        "ðŸ• ÙˆØ¶Ø¹ÛŒØª: Ø¯Ø± Ø§Ù†ØªØ¸Ø§Ø± Ø¨Ø±Ø±Ø³ÛŒ\n\n"
        "Ù¾Ø³ Ø§Ø² Ø¨Ø±Ø±Ø³ÛŒ Ù…Ø¯ÛŒØ±ÛŒØªØŒ Ù†ØªÛŒØ¬Ù‡ Ø¨Ø±Ø§ÛŒ Ø´Ù…Ø§ Ø§Ø±Ø³Ø§Ù„ Ù…ÛŒâ€ŒØ´ÙˆØ¯.",
        reply_markup=build_back_keyboard(),
    )


async def admin_remittance_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin_user(query.from_user.id):
        await query.answer("Ø¯Ø³ØªØ±Ø³ÛŒ Ù…Ø¬Ø§Ø² Ù†ÛŒØ³Øª.", show_alert=True)
        return

    if query.data.startswith("admin_status_"):
        status_key, tracking_code = query.data[len("admin_status_"):].rsplit("_", 1)
        request = REMITS.get(tracking_code)
        allowed_previous_statuses = {
            "processing": {"âœ… ØªØ£ÛŒÛŒØ¯ Ø´Ø¯"},
            "ready": {"âœ… ØªØ£ÛŒÛŒØ¯ Ø´Ø¯", "âš™ï¸ Ø¯Ø± Ø­Ø§Ù„ Ù¾Ø±Ø¯Ø§Ø²Ø´"},
            "paid": {"ðŸ’° Ø¢Ù…Ø§Ø¯Ù‡ Ø¯Ø±ÛŒØ§ÙØª"},
            "cancelled": {"âœ… ØªØ£ÛŒÛŒØ¯ Ø´Ø¯", "âš™ï¸ Ø¯Ø± Ø­Ø§Ù„ Ù¾Ø±Ø¯Ø§Ø²Ø´", "ðŸ’° Ø¢Ù…Ø§Ø¯Ù‡ Ø¯Ø±ÛŒØ§ÙØª"},
        }
        if not request or request.get("status") not in allowed_previous_statuses.get(status_key, set()):
            await query.answer("Ø§ÛŒÙ† ØªØºÛŒÛŒØ± ÙˆØ¶Ø¹ÛŒØª Ø¯Ø± Ù…Ø±Ø­Ù„Ù‡ ÙØ¹Ù„ÛŒ Ù…Ø¬Ø§Ø² Ù†ÛŒØ³Øª.", show_alert=True)
            return
        request = set_remittance_status(tracking_code, status_key)
        await context.bot.send_message(
            chat_id=request["customer_chat_id"],
            text=(
                "ðŸ”” ÙˆØ¶Ø¹ÛŒØª Ø­ÙˆØ§Ù„Ù‡ Ø´Ù…Ø§ ØªØºÛŒÛŒØ± Ú©Ø±Ø¯.\n"
                f"ðŸ†” Ú©Ø¯ Ù¾ÛŒÚ¯ÛŒØ±ÛŒ: {tracking_code}\n"
                f"ðŸ• ÙˆØ¶Ø¹ÛŒØª: {request['status']}"
            ),
        )
        await query.edit_message_caption(
            caption=format_admin_remittance(request),
            reply_markup=build_admin_status_keyboard(tracking_code, request["status"]),
        )
        await query.answer("ÙˆØ¶Ø¹ÛŒØª Ùˆ Ù…Ø´ØªØ±ÛŒ Ø¨Ù‡â€ŒØ±ÙˆØ²Ø±Ø³Ø§Ù†ÛŒ Ø´Ø¯Ù†Ø¯.")
        return

    if query.data.startswith("admin_message_"):
        tracking_code = query.data[len("admin_message_"):]
        request = REMITS.get(tracking_code)
        if not request:
            await query.answer("Ø­ÙˆØ§Ù„Ù‡ Ù¾ÛŒØ¯Ø§ Ù†Ø´Ø¯.", show_alert=True)
            return
        context.user_data["admin_message_tracking_code"] = tracking_code
        await query.answer("Ù…ØªÙ† Ù¾ÛŒØ§Ù… Ø±Ø§ Ø§Ø±Ø³Ø§Ù„ Ú©Ù†ÛŒØ¯.")
        await context.bot.send_message(
            chat_id=query.from_user.id,
            text=f"ðŸ’¬ Ù…ØªÙ† Ù¾ÛŒØ§Ù… Ø¨Ø±Ø§ÛŒ Ù…Ø´ØªØ±ÛŒ Ø­ÙˆØ§Ù„Ù‡ {tracking_code} Ø±Ø§ Ø§Ø±Ø³Ø§Ù„ Ú©Ù†ÛŒØ¯:",
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
        await query.answer("Ø¯Ø³ØªÙˆØ± Ù…Ø¯ÛŒØ±ÛŒØªÛŒ Ù†Ø§Ø´Ù†Ø§Ø®ØªÙ‡ Ø§Ø³Øª.", show_alert=True)
        return

    request = REMITS.get(tracking_code)
    if not request:
        await query.answer("Ø¯Ø±Ø®ÙˆØ§Ø³Øª Ù¾ÛŒØ¯Ø§ Ù†Ø´Ø¯.", show_alert=True)
        return

    if action == "approve":
        request["status"] = "âœ… ØªØ£ÛŒÛŒØ¯ Ø´Ø¯"
        customer_text = (
            f"âœ… Ø­ÙˆØ§Ù„Ù‡ Ø´Ù…Ø§ Ø¨Ø§ Ú©Ø¯ {tracking_code} ØªÙˆØ³Ø· Ù…Ø¯ÛŒØ±ÛŒØª ØªØ£ÛŒÛŒØ¯ Ø´Ø¯.\n"
            "ÙˆØ¶Ø¹ÛŒØª: âœ… ØªØ£ÛŒÛŒØ¯ Ø´Ø¯\n\n"
            "Ø§ÛŒÙ† Ù†Ù…ÙˆÙ†Ù‡ Ø¢Ø²Ù…Ø§ÛŒØ´ÛŒ Ù¾Ø±Ø¯Ø§Ø®Øª ÛŒØ§ Ø§Ù†ØªÙ‚Ø§Ù„ ÙˆØ§Ù‚Ø¹ÛŒ Ø§Ù†Ø¬Ø§Ù… Ù†Ù…ÛŒâ€ŒØ¯Ù‡Ø¯."
        )
        await context.bot.send_message(chat_id=request["customer_chat_id"], text=customer_text)
        await query.edit_message_caption(
            caption=format_admin_remittance(request),
            reply_markup=build_admin_status_keyboard(tracking_code, request["status"]),
        )
    elif action == "reject":
        request["status"] = "âŒ Ø±Ø¯ Ø´Ø¯"
        await context.bot.send_message(
            chat_id=request["customer_chat_id"],
            text=f"âŒ Ø­ÙˆØ§Ù„Ù‡ Ø´Ù…Ø§ Ø¨Ø§ Ú©Ø¯ {tracking_code} Ø±Ø¯ Ø´Ø¯.\nÙˆØ¶Ø¹ÛŒØª: âŒ Ø±Ø¯ Ø´Ø¯",
        )
        await query.edit_message_caption(caption=format_admin_remittance(request), reply_markup=None)
    elif action == "more":
        await context.bot.send_message(
            chat_id=request["customer_chat_id"],
            text=(
                f"ðŸ’¬ Ù…Ø¯ÛŒØ±ÛŒØª Ø¯Ø±Ø¨Ø§Ø±Ù‡ Ø­ÙˆØ§Ù„Ù‡ {tracking_code} Ø§Ø·Ù„Ø§Ø¹Ø§Øª Ø¨ÛŒØ´ØªØ±ÛŒ Ø¯Ø±Ø®ÙˆØ§Ø³Øª Ú©Ø±Ø¯Ù‡ Ø§Ø³Øª.\n"
                "Ù„Ø·ÙØ§Ù‹ Ø¨Ø±Ø§ÛŒ Ù¾ÛŒÚ¯ÛŒØ±ÛŒ Ø¨Ø§ Ù¾Ø´ØªÛŒØ¨Ø§Ù†ÛŒ ØªÙ…Ø§Ø³ Ø¨Ú¯ÛŒØ±ÛŒØ¯.\n"
                "ðŸ• ÙˆØ¶Ø¹ÛŒØª: Ø¯Ø± Ø§Ù†ØªØ¸Ø§Ø± Ø¨Ø±Ø±Ø³ÛŒ"
            ),
        )
        await query.answer("Ø¯Ø±Ø®ÙˆØ§Ø³Øª Ø§Ø·Ù„Ø§Ø¹Ø§Øª Ø¨ÛŒØ´ØªØ± Ø¨Ø±Ø§ÛŒ Ù…Ø´ØªØ±ÛŒ Ø§Ø±Ø³Ø§Ù„ Ø´Ø¯.")
        return
    await query.answer("ÙˆØ¶Ø¹ÛŒØª Ø¯Ø±Ø®ÙˆØ§Ø³Øª Ø¨Ù‡â€ŒØ±ÙˆØ²Ø±Ø³Ø§Ù†ÛŒ Ø´Ø¯.")


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data in {"main", "account_main"}:
        await query.edit_message_text(
            MAIN_MENU_TEXT,
            reply_markup=build_main_keyboard(),
        )
    elif query.data == "remittance":
        await query.edit_message_text(
            "ðŸŒ Ø­ÙˆØ§Ù„Ù‡ Ùˆ Ø§Ù†ØªÙ‚Ø§Ù„ Ù¾ÙˆÙ„\n\nØ¨Ø±Ø§ÛŒ Ø«Ø¨Øª ÛŒÚ© Ø¯Ø±Ø®ÙˆØ§Ø³Øª Ø¢Ø²Ù…Ø§ÛŒØ´ÛŒ Ú¯Ø²ÛŒÙ†Ù‡ Ø²ÛŒØ± Ø±Ø§ Ø§Ù†ØªØ®Ø§Ø¨ Ú©Ù†ÛŒØ¯.",
            reply_markup=build_remittance_menu_keyboard(),
        )
    elif query.data == "remittance_send":
        await start_remittance(update, context)
    elif query.data == "buy_sell":
        await query.edit_message_text(
            "ðŸ’± Ø®Ø±ÛŒØ¯ Ùˆ ÙØ±ÙˆØ´ Ø§Ø±Ø²\n\nÙ„Ø·ÙØ§Ù‹ Ù†ÙˆØ¹ Ù…Ø¹Ø§Ù…Ù„Ù‡Ù” Ù…ÙˆØ±Ø¯ Ù†Ø¸Ø± Ø±Ø§ Ø§Ù†ØªØ®Ø§Ø¨ Ú©Ù†ÛŒØ¯.",
            reply_markup=build_buy_sell_keyboard(),
        )
    elif query.data in {"buy_currency", "sell_currency"}:
        await query.edit_message_text(
            "âš ï¸ Ø«Ø¨Øª ÛŒØ§ ØªØ£ÛŒÛŒØ¯ Ù…Ø¹Ø§Ù…Ù„Ù‡ Ø¯Ø± Ø­Ø§Ù„ Ø­Ø§Ø¶Ø± Ø¯Ø± Ø¯Ø³ØªØ±Ø³ Ù†ÛŒØ³Øª.\n"
            "Ù‡ÛŒÚ† Ù…Ø¹Ø§Ù…Ù„Ù‡â€ŒØ§ÛŒ Ø«Ø¨Øª ÛŒØ§ Ø§Ù†Ø¬Ø§Ù… Ù†Ø´Ø¯Ù‡ Ø§Ø³Øª.",
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
            "ðŸ”Ž Ú©Ø¯ Ù¾ÛŒÚ¯ÛŒØ±ÛŒ Ø­ÙˆØ§Ù„Ù‡ Ø±Ø§ ÙˆØ§Ø±Ø¯ Ú©Ù†ÛŒØ¯:\nÙ…Ø«Ø§Ù„: LA-20260923-1001",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("â¬…ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª", callback_data="account")]]
            ),
        )
    elif query.data == "account_support":
        context.user_data["account_support"] = True
        await query.edit_message_text(
            "ðŸ’¬ Ù¾ÛŒØ§Ù… Ø®ÙˆØ¯ Ø±Ø§ Ø¨Ø±Ø§ÛŒ Ù¾Ø´ØªÛŒØ¨Ø§Ù†ÛŒ Ø§Ø±Ø³Ø§Ù„ Ú©Ù†ÛŒØ¯.\n"
            "Ù¾ÛŒØ§Ù… Ø´Ù…Ø§ Ø¨Ù‡ Ø­ÙˆØ§Ù„Ù‡ ÙØ¹Ø§Ù„ Ù…Ø±ØªØ¨Ø· Ù…ÛŒâ€ŒØ´ÙˆØ¯.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("â¬…ï¸ Ø¨Ø§Ø²Ú¯Ø´Øª", callback_data="account")]]
            ),
        )
    elif query.data == "account_security":
        await query.edit_message_text(
            "ðŸ” Ø§Ø·Ù„Ø§Ø¹Ø§Øª Ø­Ø³Ø§Ø¨ Ø´Ù…Ø§ Ù…Ø­Ø±Ù…Ø§Ù†Ù‡ Ø§Ø³Øª Ùˆ Ø¨Ù‡ Ú©Ø§Ø±Ø¨Ø±Ø§Ù† Ø¯ÛŒÚ¯Ø± Ù†Ù…Ø§ÛŒØ´ Ø¯Ø§Ø¯Ù‡ Ù†Ù…ÛŒâ€ŒØ´ÙˆØ¯.\n"
            "Ø§Ø² Ø§Ø·Ù„Ø§Ø¹Ø§Øª Ø´Ù…Ø§ ÙÙ‚Ø· Ø¨Ø±Ø§ÛŒ Ø§Ø±Ø§Ø¦Ù‡ Ø®Ø¯Ù…Ø§Øª Ø§Ø³ØªÙØ§Ø¯Ù‡ Ù…ÛŒâ€ŒØ´ÙˆØ¯.",
            reply_markup=build_account_keyboard(),
        )
    elif query.data == "remit_cancel":
        clear_remittance(context)
        await query.edit_message_text("âŒ Ø¯Ø±Ø®ÙˆØ§Ø³Øª Ø­ÙˆØ§Ù„Ù‡ Ù„ØºÙˆ Ø´Ø¯.", reply_markup=build_main_keyboard())
    elif query.data.startswith("remit_currency_"):
        request = context.user_data.get("remittance")
        if request and request.get("step") == "currency":
            request["currency"] = query.data.rsplit("_", 1)[1]
            request["step"] = "city"
            await query.edit_message_text("ðŸ“ Ø´Ù‡Ø± Ø¯Ø±ÛŒØ§ÙØª Ø¯Ø± Ø§ÙØºØ§Ù†Ø³ØªØ§Ù† Ø±Ø§ Ø§Ù†ØªØ®Ø§Ø¨ Ú©Ù†ÛŒØ¯:", reply_markup=build_remittance_cities_keyboard())
    elif query.data.startswith("remit_city_"):
        request = context.user_data.get("remittance")
        cities = ("Ú©Ø§Ø¨Ù„", "Ù‡Ø±Ø§Øª", "Ù…Ø²Ø§Ø± Ø´Ø±ÛŒÙ", "Ù‚Ù†Ø¯Ù‡Ø§Ø±", "Ø¬Ù„Ø§Ù„â€ŒØ¢Ø¨Ø§Ø¯", "Ø³Ø§ÛŒØ±")
        if request and request.get("step") == "city":
            request["city"] = cities[int(query.data.rsplit("_", 1)[1])]
            request["step"] = "document"
            await query.edit_message_text("ðŸªª Ø¹Ú©Ø³ ØªØ°Ú©Ø±Ù‡ ÛŒØ§ Ù¾Ø§Ø³Ù¾ÙˆØ±Øª Ú¯ÛŒØ±Ù†Ø¯Ù‡ Ø±Ø§ Ø§Ø±Ø³Ø§Ù„ Ú©Ù†ÛŒØ¯:", reply_markup=build_back_keyboard("remit_cancel"))
    elif query.data == "remit_confirm":
        await confirm_remittance(update, context)
    elif query.data.startswith("admin_"):
        await admin_remittance_callback(update, context)
    elif query.data == "transactions":
        await query.edit_message_text(
            "ðŸš§ Ø§ÛŒÙ† Ø¨Ø®Ø´ Ø¨Ù‡â€ŒØ²ÙˆØ¯ÛŒ ÙØ¹Ø§Ù„ Ø®ÙˆØ§Ù‡Ø¯ Ø´Ø¯.\n\nØ¨Ø±Ø§ÛŒ Ù†Ø³Ø®Ù‡ Ø¢ÛŒÙ†Ø¯Ù‡ØŒ Ø§ÛŒÙ† Ú¯Ø²ÛŒÙ†Ù‡ Ø¨Ù‡ workflow Ø§Ø®ØªØµØ§ØµÛŒ SARWARI EXCHANGE Ù…ØªØµÙ„ Ø®ÙˆØ§Ù‡Ø¯ Ø´Ø¯.",
            reply_markup=build_back_keyboard(),
        )
    elif query.data == "support":
        await query.edit_message_text(
            "ðŸ†˜ Ù¾Ø´ØªÛŒØ¨Ø§Ù†ÛŒ SARWARI EXCHANGE\n\n"
            "Ø¨Ø±Ø§ÛŒ Ø¯Ø±ÛŒØ§ÙØª Ø±Ø§Ù‡Ù†Ù…Ø§ÛŒÛŒØŒ Ø§Ø³ØªØ¹Ù„Ø§Ù… Ù†Ø±Ø®ØŒ Ù¾ÛŒÚ¯ÛŒØ±ÛŒ Ø®Ø¯Ù…Ø§Øª Ùˆ Ø§Ø±ØªØ¨Ø§Ø· Ø¨Ø§ ØµØ±Ø§ÙÛŒØŒ Ø§Ø² Ø·Ø±ÛŒÙ‚ ÙˆØ§ØªØ³Ø§Ù¾ Ø¨Ø§ Ù…Ø§ Ø¯Ø± ØªÙ…Ø§Ø³ Ø¨Ø§Ø´ÛŒØ¯.\n\n"
            "ðŸ¤ Ù¾Ø´ØªÛŒØ¨Ø§Ù†ÛŒ Ùˆ Ù¾Ø§Ø³Ø®Ú¯ÙˆÛŒÛŒ\n"
            "ðŸ“± ÙˆØ§ØªØ³Ø§Ù¾: https://wa.me/message/FCRIX7IXIPU7I1",
            reply_markup=build_support_keyboard(),
        )
    elif query.data == "rates":
        await show_rates(update, context, edit=True)
    elif query.data == "refresh":
        try:
            refreshed = await RATES_SERVICE.refresh_reference_rates()
            await show_rates(
                update,
                context,
                edit=True,
                reference_result=refreshed,
            )
        except (requests.RequestException, ValueError, TypeError, KeyError) as error:
            logging.warning("Currency refresh failed: %s", error)
            await query.edit_message_text(RATE_ERROR, reply_markup=build_back_keyboard("refresh"))
    elif query.data == "refresh_gold":
        try:
            refreshed = await RATES_SERVICE.refresh_gold()
            await show_gold(update, context, edit=True, gold_result=refreshed)
        except (requests.RequestException, ValueError, TypeError, KeyError) as error:
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
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("rates", rates))
    app.add_handler(CommandHandler("gold", gold))
    app.add_handler(CommandHandler("news", news))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, web_app_callback))
    app.add_handler(MessageHandler(filters.PHOTO | (filters.TEXT & ~filters.COMMAND), remittance_message))

    logging.info("Bot is running")
    try:
        app.run_polling()
    finally:
        fcntl.flock(instance_lock, fcntl.LOCK_UN)
        instance_lock.close()


if __name__ == "__main__":
    main()
