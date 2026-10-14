"""ویرایشگر کامل رابط کاربری ربات.

این فایل عمداً فقط رابط کاربری مشتری را مدیریت می‌کند؛ بخش «مدیریت/پنل ادمین»
از ویرایشگر حذف شده تا تغییرات ادمین از این قسمت آسیب نبیند.

امکانات:
- ویرایش متن صفحه‌ها با حفظ MessageEntity و Custom Emoji (Premium Emoji)
- ویرایش نام تمام دکمه‌های ثبت‌شده
- آیکن Custom Emoji برای دکمه‌ها (در Bot APIهای جدید)
- جابه‌جایی دکمه‌ها
- مخفی/نمایش کردن دکمه‌ها
- تنظیم تعداد دکمه در هر ردیف؛ پیش‌فرض همیشه یک دکمه در هر ردیف است
- نگهداری تنظیمات در دیتابیس
"""

import datetime
import json
import logging
import re
import threading
from typing import Any

import database as db
import cache
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

logger = logging.getLogger(__name__)

# ⚡ PERFORMANCE:
# _ensure() used to execute a full DB transaction containing several
# CREATE TABLE IF NOT EXISTS / ALTER TABLE statements on EVERY outgoing
# user message because bot.py globally routes send/edit calls through
# apply_auto_text(). With Turso this is a network round-trip per message and
# was the main source of the 2-3 second latency.
#
# Keep the editor schema initialized once per process. Writes/migrations still
# use the same transaction; only the repeated read-path initialization is
# removed.
_SCHEMA_READY = False
_SCHEMA_INIT_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# رجیستری صفحه‌های مشتری
# ---------------------------------------------------------------------------
# callbackهایی که با _ تمام می‌شوند، الگوی callbackهای داینامیک هستند؛ مثلاً
# buy_ برای buy_plan_a / buy_plan_b.
SCREENS: dict[str, dict[str, Any]] = {
    # admin_panel: حذف شده از Editor چون Reply Keyboard ادمین به ۶ دسته تبدیل شد.
    # (backward compat: screens_keyboard و get_order همچنان کار می‌کنند)
    # "admin_panel": {...},
    "main_reply": {"category": "start", "label": "⌨️ منوی پایین کاربر", "default": "", "buttons": [
        ("plans", "🛒 خرید اشتراک"), ("free_test", "🎁 تست رایگان"), ("services", "📱 سرویس‌های من"),
        ("wallet", "💰 کیف پول"), ("referral", "👥 دعوت دوستان"), ("profile", "👤 پروفایل من"),
        ("support", "👨‍💻 پشتیبانی"), ("guides", "📚 راهنما"), ("agency", "🤝 درخواست نمایندگی"), ("agent_manage", "🤝 مدیریت نمایندگی"),
    ]},
    "start": {"category": "start", "label": "🚀 شروع و منوی اصلی", "default": "👋 خوش آمدید", "buttons": [
        ("plans", "🛒 خرید اشتراک"), ("buy_plan_test", "🎁 تست رایگان"),
        ("my_configs", "📱 سرویس‌های من"), ("wallet", "💰 کیف پول"),
        ("referral", "👥 دعوت دوستان و کسب درآمد"), ("profile", "👤 پروفایل من"),
        ("support", "👨‍💻 پشتیبانی"), ("user_guides", "📚 راهنما"),
    ]},
    "buy_plans": {"category": "shop", "label": "🛒 متن معرفی خرید اشتراک", "default": "🛒 انتخاب سرویس مناسب", "buttons": [
        ("plans_vip", "🚀 سرور VIP (V2Ray)"), ("cbuild_start", "🚀 کانفیگ خودتو بساز"), ("back", "🔙 بازگشت"),
    ]},
    "plans": {"category": "shop", "label": "🛒 خرید اشتراک", "default": "🛒 انتخاب سرویس مناسب", "buttons": [
        ("plans_vip", "🚀 سرور VIP (V2Ray)"), ("noop", "✨〰️〰️〰️〰️〰️✨"),
        ("cbuild_start", "🚀 کانفیگ خودتو بساز (ویژه VIP) 🛠"), ("payg_buy", "⚡ خرید سرویس PAYG"), ("noop", "✨〰️〰️〰️〰️〰️✨"),
        ("back", "🔙 بازگشت به منوی اصلی"),
    ]},
    "free_test": {"category": "shop", "label": "🎁 تست رایگان", "default": "🎁 تست رایگان", "buttons": [
        ("pay_wallet_", "⚡️ همین الان تست رایگان بگیر"), ("plans", "🔙 بازگشت"),
    ]},
    "vip_category_list": {"category": "shop", "label": "⭐ دسته‌بندی VIP", "default": "⭐ دسته‌بندی‌های VIP", "buttons": [
        ("vipcat_", "🚀 دسته‌بندی VIP"), ("cbuild_start", "🚀 کانفیگ خودتو بساز"), ("plans", "🔙 بازگشت"),
    ]},
    "vip_plans": {"category": "shop", "label": "🚀 پلن‌های VIP", "default": "🚀 پلن‌های VIP", "buttons": [
        ("buy_", "📅 پلن"), ("plans_vip", "🔙 بازگشت به دسته‌بندی‌ها"),
    ]},
    "plan_select": {"category": "shop", "label": "📅 انتخاب پلن", "default": "📅 انتخاب پلن", "buttons": [
        ("buy_", "📅 پلن"), ("plans", "🔙 بازگشت"),
    ]},
    "custom_build": {"category": "shop", "label": "🛠 کانفیگ خودتو بساز", "default": "🛠 کانفیگ خودتو بساز", "buttons": [
        ("cbuild_pay_wallet", "👛 پرداخت از کیف پول"), ("cbuild_pay_online", "🌐 پرداخت آنلاین"),
        ("cbuild_pay_card", "💳 کارت به کارت"), ("discount_cbuild", "🎟 ثبت کد تخفیف"),
        ("plans", "🔙 انصراف"),
    ]},
    "cbuild_payment_method": {"category": "shop", "label": "💳 روش پرداخت کانفیگ", "default": "💳 روش پرداخت", "buttons": [
        ("cbuild_pay_wallet", "👛 پرداخت از کیف پول"), ("cbuild_pay_online", "🌐 پرداخت آنلاین"),
        ("cbuild_pay_card", "💳 کارت به کارت"), ("discount_cbuild", "🎟 ثبت کد تخفیف"), ("plans", "🔙 انصراف"),
    ]},
    "cbuild_pay_wallet": {"category": "finance", "label": "👛 پرداخت کیف پول", "default": "👛 پرداخت از کیف پول", "buttons": [
        ("cbuild_change_payment", "🔄 روش پرداخت دیگر"), ("plans", "🔙 بازگشت"),
    ]},
    "cbuild_pay_online": {"category": "finance", "label": "🌐 پرداخت آنلاین", "default": "🌐 پرداخت آنلاین", "buttons": [
        ("cbuild_change_payment", "🔄 روش پرداخت دیگر"), ("plans", "🔙 بازگشت"),
    ]},
    "cbuild_pay_card": {"category": "finance", "label": "💳 پرداخت کارت به کارت", "default": "💳 پرداخت کارت به کارت", "buttons": [
        ("cbuild_change_payment", "🔄 روش پرداخت دیگر"), ("plans", "🔙 بازگشت"),
    ]},
    "plan_payment_method": {"category": "shop", "label": "💳 روش پرداخت خرید", "default": "💳 روش پرداخت", "buttons": [
        ("pay_wallet_", "👛 پرداخت از کیف پول"), ("pay_online_", "🌐 پرداخت آنلاین"),
        ("pay_card_", "💳 پرداخت کارت به کارت"), ("pay_crypto_", "💎 پرداخت ارز دیجیتال"), ("discount_plan_", "🎟 ثبت کد تخفیف"), ("plans", "🔙 بازگشت"),
    ]},
    "plan_pay_wallet": {"category": "finance", "label": "👛 خرید با کیف پول", "default": "👛 پرداخت از کیف پول", "buttons": [
        ("plans", "🔙 بازگشت"),
    ]},
    "plan_pay_online": {"category": "finance", "label": "🌐 خرید آنلاین", "default": "🌐 پرداخت آنلاین", "buttons": [
        ("plans", "🔙 بازگشت"),
    ]},
    "plan_pay_card": {"category": "finance", "label": "💳 خرید کارت به کارت", "default": "💳 پرداخت کارت به کارت", "buttons": [
        ("plans", "🔙 بازگشت"),
    ]},
    "discount_code_entry": {"category": "shop", "label": "🎟 ورود کد تخفیف", "default": "🎟 کد تخفیف خود را وارد کنید:", "buttons": [
        ("plans", "🔙 انصراف"), ("wallet", "🔙 بازگشت"),
    ]},
    "services": {"category": "services", "label": "📱 سرویس‌های من", "default": "📱 سرویس‌های شما", "buttons": [
        ("my_configs_vip", "🚀 سرویس‌های VIP من"), ("back", "🏠 بازگشت"),
    ]},
    "my_configs_empty": {"category": "services", "label": "📱 سرویس‌های من — خالی", "default": "📱 شما هنوز هیچ سرویسی خریداری نکرده‌اید.", "buttons": [("back", "🏠 بازگشت به منوی اصلی")]},
    "my_configs_has": {"category": "services", "label": "📱 سرویس‌های من", "default": "📱 سرویس‌های شما", "buttons": [
        ("my_configs_vip", "🚀 سرویس‌های VIP من"), ("back", "🏠 بازگشت به منوی اصلی"),
    ]},
    "my_configs_list_empty": {"category": "services", "label": "📋 لیست سرویس‌ها — خالی", "default": "📋 سرویسی برای نمایش وجود ندارد.", "buttons": [("my_configs", "🔙 بازگشت")]},
    "my_configs_list_has": {"category": "services", "label": "📋 لیست سرویس‌ها", "default": "📋 سرویس‌های شما", "buttons": [
        ("viewconfig_", "🚀 سرویس"), ("back", "🔙 بازگشت"),
    ]},
    "service_list_vip": {"category": "services", "label": "🚀 سرویس‌های VIP", "default": "🚀 سرویس‌های VIP", "buttons": [
        ("viewconfig_", "🚀 سرویس"), ("my_configs", "🔙 بازگشت"),
    ]},
    "config_detail": {"category": "services", "label": "📱 جزئیات سرویس", "default": "📱 جزئیات سرویس", "buttons": [
        ("renewcfg_", "🔁 تمدید سرویس"), ("viewqr_", "🖼 مشاهده کیوآرکد"),
        ("mirrorconfigs_", "🔗 دریافت کانفیگ‌های تکی"), ("usersvclink_", "🔄 تغییر لینک ساب"),
        ("usersvcenable_", "▶️ فعال‌کردن لینک ساب"), ("usersvcdisable_", "⏸ غیرفعال‌کردن لینک ساب"),
        ("delconfig_", "🗑 حذف سرویس"), ("back", "🔙 بازگشت"),
    ]},
    "config_delete_confirm": {"category": "services", "label": "🗑 تأیید حذف سرویس", "default": "⚠️ مطمئنی می‌خوای این سرویس رو حذف کنی؟", "buttons": [
        ("delconfirm_", "✅ بله، حذف کن"), ("viewconfig_", "❌ انصراف"),
    ]},
    "renew_done": {"category": "services", "label": "✅ نتیجه تمدید", "default": "✅ تمدید سرویس «{service_name}» با موفقیت انجام شد.\n\n📦 حجم اضافه: {added_volume}\n⏳ زمان اضافه: {added_days}\n\n📦 وضعیت فعلی: {current_package}\n🔗 لینک ساب شما تغییر نکرده است.", "buttons": [("back", "🔙 بازگشت")]},
    "renew_menu": {"category": "services", "label": "🔁 تمدید سرویس", "default": "🔁 تمدید سرویس", "buttons": [
        ("renewdays_", "⏳ انتخاب روز"), ("renewvol_", "📦 انتخاب حجم"),
        ("renew_cancel", "🔙 بازگشت"),
    ]},
    "wallet": {"category": "finance", "label": "💰 کیف پول", "default": "💰 کیف پول", "buttons": [
        ("charge", "💳 شارژ کیف پول"), ("use_discount", "🎟 ثبت کد تخفیف"), ("transactions", "📋 تراکنش‌های من"), ("back", "🏠 بازگشت"),
    ]},
    "wallet_free": {"category": "finance", "label": "💰 کیف پول آزاد", "default": "💰 کیف پول آزاد", "buttons": [("back", "🔙 بازگشت")]},
    "wallet_locked": {"category": "finance", "label": "🔒 کیف پول مسدود", "default": "🔒 کیف پول مسدود", "buttons": [("back", "🔙 بازگشت")]},
    "wallet_transactions": {"category": "finance", "label": "📋 تراکنش‌ها", "default": "📋 تراکنش‌های کیف پول", "buttons": [("back", "🔙 بازگشت")]},
    "wallet_charge": {"category": "finance", "label": "💳 شارژ کیف پول", "default": "💳 شارژ کیف پول", "buttons": [
        ("charge_", "💳 مبلغ شارژ"), ("wallet", "🔙 بازگشت"),
    ]},
    "walletcharge_method": {"category": "finance", "label": "💳 روش شارژ", "default": "💳 روش شارژ", "buttons": [
        ("chargepay_card_", "💳 کارت به کارت"), ("chargepay_online_", "🌐 پرداخت آنلاین"), ("charge", "🔙 بازگشت"),
    ]},
    "walletcharge_pay_card": {"category": "finance", "label": "💳 رسید شارژ", "default": "💳 پرداخت کارت به کارت", "buttons": [
        ("walletcharge_method", "🔄 روش پرداخت دیگر"), ("wallet", "🔙 بازگشت"),
    ]},
    "walletcharge_pay_online": {"category": "finance", "label": "🌐 شارژ آنلاین", "default": "🌐 پرداخت آنلاین", "buttons": [("wallet", "🔙 بازگشت")]},
    "referral": {"category": "finance", "label": "👥 دعوت دوستان", "default": (
        "👥 دعوت دوستان و کسب درآمد 💸\n\n"
        "دوستانتو دعوت کن و به‌ازای هر دعوت موفق، {reward_amount} تومان پاداش نقدی بگیر! 🎁\n\n"
        "🔗 لینک اختصاصی شما:\n{invite_link}\n\n"
        "🔑 کد اختصاصی: {invite_code}\n\n"
        "👤 تعداد دعوت: {invited_count}\n"
        "✅ دعوت‌های موفق: {successful_invites}\n"
        "🔓 مبلغ آزاد شده: {released_amount} تومان\n"
        "🔒 مبلغ در انتظار: {locked_wallet} تومان\n\n"
        "{condition_text}"
    ), "buttons": [("back", "🔙 بازگشت")]},
    "profile": {"category": "services", "label": "👤 پروفایل", "default": "👤 پروفایل شما", "buttons": [
        ("wallet_free", "💰 کیف پول آزاد"), ("wallet_locked", "🔒 کیف پول مسدود"), ("purchase_history", "🛒 تاریخچه خرید"),
        ("transactions", "📋 تاریخچه تراکنش"), ("referral", "🔗 لینک دعوت اختصاصی"), ("back", "🏠 بازگشت به منوی اصلی"),
    ]},
    "purchase_history": {"category": "services", "label": "🧾 تاریخچه خرید", "default": (
        "🛒 تاریخچه خرید شما:\n\n"
        "{purchase_reports}\n\n"
        "💰 مجموع خرید: {total_spent} تومان\n"
        "📊 تعداد خریدها: {purchase_count}"
    ), "buttons": [("profile", "🔙 بازگشت")]},
    "support": {"category": "services", "label": "👨‍💻 پشتیبانی", "default": "👨‍💻 پشتیبانی", "buttons": [("ticket", "🎫 ارسال تیکت"), ("back", "🏠 بازگشت")]},
    "ticket_write": {"category": "services", "label": "🎫 ارسال تیکت", "default": "🎫 متن تیکت خود را ارسال کنید:", "buttons": [("ticket_cancel", "❌ انصراف")]},
    "guides_empty": {"category": "services", "label": "📚 راهنما — خالی", "default": "📚 راهنما و آموزش‌ها", "buttons": [("back", "🔙 بازگشت")]},
    "guides_has": {"category": "services", "label": "📚 راهنما", "default": "📚 راهنما و آموزش‌ها", "buttons": [("guideopen_", "📖 راهنما"), ("user_guides", "📚 فهرست راهنماها"), ("back", "🏠 بازگشت به منوی اصلی")]},
    "join_confirmed": {"category": "start", "label": "✅ عضویت تأیید شد", "default": "منوی اصلی در پایین صفحه فعال شد ✅", "buttons": []},
    "start_join_required": {"category": "start", "label": "🔐 عضویت اجباری", "default": "⚠️ برای استفاده از ربات ابتدا در کانال‌های زیر عضو شوید:", "buttons": []},
    "start_welcome": {"category": "start", "label": "👋 خوش‌آمدگویی", "default": "👋 خوش آمدید", "buttons": [
        ("plans", "🛒 خرید اشتراک"), ("buy_plan_test", "🎁 تست رایگان"), ("my_configs", "📱 سرویس‌های من"),
        ("wallet", "💰 کیف پول"), ("referral", "👥 دعوت دوستان"), ("profile", "👤 پروفایل"),
        ("support", "👨‍💻 پشتیبانی"), ("user_guides", "📚 راهنما"),
    ]},
    "agency_request": {"category": "services", "label": "🤝 درخواست نمایندگی", "default": "🤝 درخواست نمایندگی", "buttons": [("back", "🔙 بازگشت")]},
    "agent_manage": {"category": "services", "label": "🤝 مدیریت نمایندگی", "default": "🤝 مدیریت نمایندگی", "buttons": [("agent_stats", "📊 آمار نمایندگی"), ("agent_prefix", "🏷 نام دلخواه ساخت کانفیگ"), ("back", "🔙 بازگشت")]},
    "config_delivery": {"category": "services", "label": "📤 تحویل کانفیگ", "default": (
        "✅ سرویس با موفقیت ایجاد شد\n\n"
        "👤 نام کاربری سرویس : {name}\n"
        "🇺🇳 لوکیشن: {location}\n"
        "⏳ مدت زمان: {days}\n"
        "🗜 حجم سرویس: {volume}\n"
        "👤 تعداد کاربر: {users}\n\n"
        "لینک اتصال:\n{sub_link}\n\n"
        "🧑‍🦯 شما میتوانید شیوه اتصال را با فشردن دکمه زیر دریافت کنید."
    ), "buttons": [("guide", "🧑‍🦯 دریافت روش اتصال")]},
    "receipt_submitted": {"category": "finance", "label": "🧾 ارسال رسید", "default": "رسید شما ارسال شد.", "buttons": [("back", "🏠 بازگشت به صفحه اصلی")]},
    "card_payment_actions": {"category": "finance", "label": "💳 اقدامات پرداخت کارت‌به‌کارت", "default": "", "buttons": [
        ("changepay_", "🔄 انتخاب روش پرداخت دیگر"),
    ]},
    "insufficient_balance": {"category": "finance", "label": "💰 موجودی ناکافی", "default": "❌ موجودی کیف پول کافی نیست.", "buttons": [
        ("wallet", "💵 شارژ کیف پول"), ("back", "🔙 بازگشت"),
    ]},
}

BUTTON_BUNDLES = {
    "payments": {
        "label": "💳 دکمه‌های پرداخت",
        "screen_keys": {"plan_payment_method", "cbuild_payment_method", "walletcharge_method", "plan_pay_wallet", "plan_pay_online", "plan_pay_card", "cbuild_pay_wallet", "cbuild_pay_online", "cbuild_pay_card", "wallet_charge", "walletcharge_pay_card", "walletcharge_pay_online"},
        "callback_keys": {"cbuild_pay_wallet", "cbuild_pay_online", "cbuild_pay_card", "cbuild_change_payment", "walletcharge_method", "charge_custom"},
        "callback_prefixes": ("pay_wallet_", "pay_online_", "pay_card_", "pay_crypto_", "discount_plan_", "changepay_", "charge_", "chargepay_card_", "chargepay_online_"),
    },
    "wallet": {
        "label": "💰 دکمه‌های کیف پول",
        "screen_keys": {"wallet", "wallet_free", "wallet_locked", "wallet_transactions", "wallet_charge", "walletcharge_method", "walletcharge_pay_card", "walletcharge_pay_online"},
        "callback_keys": {"wallet", "charge", "transactions", "use_discount"},
        "callback_prefixes": ("charge_", "chargepay_", "wallet", "transactions"),
    },
    "purchase": {
        "label": "🛒 دکمه‌های خرید و پلن‌ها",
        "screen_keys": {"buy_plans", "plans", "free_test", "vip_category_list", "vip_plans", "plan_select", "custom_build", "plan_payment_method", "cbuild_payment_method", "discount_code_entry"},
        "callback_keys": {"plans", "plans_vip", "cbuild_start", "discount_cbuild"},
        "callback_prefixes": ("buy_", "vipcat_", "discount_plan_", "discount_cbuild", "payg_buy"),
    },
    "services": {
        "label": "📱 دکمه‌های سرویس‌ها",
        "screen_keys": {"services", "my_configs_empty", "my_configs_has", "my_configs_list_empty", "my_configs_list_has", "service_list_vip", "config_detail", "config_delete_confirm", "renew_done", "renew_menu"},
        "callback_keys": {"my_configs", "my_configs_vip", "service_search", "renew_cancel"},
        "callback_prefixes": ("viewconfig_", "renewcfg_", "viewqr_", "mirrorconfigs_", "usersvclink_", "usersvcenable_", "usersvcdisable_", "delconfig_", "delconfirm_", "renewdays_", "renewvol_"),
    },
    "shared": {
        "label": "🔁 دکمه‌های عمومی و ناوبری",
        "screen_keys": {"start", "main_reply", "support", "guides_has", "guides_empty", "profile", "receipt_submitted", "card_payment_actions", "insufficient_balance"},
        "callback_keys": {"back", "plans", "wallet", "support", "profile", "referral", "user_guides", "ticket", "my_tickets", "agency", "agent_manage", "renew_cancel", "ticket_cancel"},
        "callback_prefixes": ("guideopen_", "ticketreply_", "changepay_"),
    },
}


CATEGORIES = {
    # ——— کاربران ———
    "start":          "🚀 شروع",
    "main_menu":      "🏠 منوی اصلی",
    "shop":           "🛍 خرید",
    "renewal":        "🔁 تمدید",
    "services":       "📱 سرویس‌ها",
    "test":           "🎁 تست",
    "custom_service": "🛠 سرویس سفارشی",
    "wallet":         "💰 کیف پول",
    "payment":        "💳 پرداخت",
    "finance":        "🯧 فاکتور",
    "discount":       "🎟 تخفیف",
    "referral":       "👥 رفرال",
    "agency":         "🤝 نمایندگی",
    "active_services":"✅ سرویس‌های فعال",
    "expired_services":"❌ سرویس‌های منقضی",
    "errors":         "⚠️ پیام‌های خطا",
    "success":        "✅ پیام‌های موفقیت",
    "guides":         "📚 راهنما",
    "support":        "📞 پشتیبانی",
    # ——— مدیریت ———
    "admin":               "🛡 پنل مدیریت",
    "admin_users":         "👥 کاربران (ادمین)",
    "admin_stats":         "📊 آمار",
    "admin_tickets":       "🎫 تیکت‌ها",
    "admin_broadcast":     "📢 پیام همگانی",
    "admin_orders":        "📥 سفارش‌ها",
    "admin_payments":      "💳 پرداخت‌ها (ادمین)",
    "admin_receipts":      "🧲 رسیدها",
    "admin_services":      "📱 سرویس‌ها (ادمین)",
    "admin_panels":        "🖥 پنل‌ها",
    "admin_vip":           "⭐ VIP",
    "admin_test":          "🎁 تست (ادمین)",
    "admin_renewal":       "🔁 تمدید (ادمین)",
    "admin_agents":        "🤝 نمایندگان",
    "admin_discounts":     "🎟 تخفیف‌ها (ادمین)",
    "admin_referral":      "👥 رفرال (ادمین)",
    "admin_wallet":        "💰 کیف پول (ادمین)",
    "admin_payg":          "⚡ Pay As You Go",
    "admin_miniapp":       "🖥 Mini App",
    "admin_settings":      "⚙️ تنظیمات",
    # ——— مابقی ———
    "all_messages": "🧩 تمام پیام‌ها و اعلان‌ها",
    "all_buttons":  "🔘 تمام دکمه‌های قابل ویرایش",
}

def _ensure() -> None:
    """Ensure UI-editor tables exist, but never hit Turso repeatedly.

    The old implementation opened a transaction and ran all CREATE/ALTER
    statements on every call. Since get_text()/get_button()/get_layout() are
    called while rendering almost every message, that turned the global UI
    editor hook into a database round-trip for every response.
    """
    global _SCHEMA_READY

    if _SCHEMA_READY:
        return

    # Only the first caller performs the migration. Other callers wait once,
    # then return without touching the database.
    with _SCHEMA_INIT_LOCK:
        if _SCHEMA_READY:
            return

        with db.transaction() as cur:
            cur.execute("""CREATE TABLE IF NOT EXISTS ui_text_overrides (
                key TEXT PRIMARY KEY,
                text TEXT NOT NULL,
                entities_json TEXT,
                updated_at TEXT NOT NULL
            )""")
            cur.execute("""CREATE TABLE IF NOT EXISTS ui_button_overrides (
                screen_key TEXT NOT NULL,
                callback_key TEXT NOT NULL,
                text TEXT NOT NULL,
                PRIMARY KEY(screen_key,callback_key)
            )""")
            cur.execute("""CREATE TABLE IF NOT EXISTS ui_button_meta (
                screen_key TEXT NOT NULL,
                callback_key TEXT NOT NULL,
                custom_emoji_id TEXT,
                hidden INTEGER NOT NULL DEFAULT 0,
                style TEXT,
                PRIMARY KEY(screen_key,callback_key)
            )""")
            try:
                cur.execute("ALTER TABLE ui_button_meta ADD COLUMN style TEXT")
            except Exception as exc:
                if "duplicate column" not in str(exc).lower():
                    raise

            cur.execute("""CREATE TABLE IF NOT EXISTS ui_layouts (
                screen_key TEXT PRIMARY KEY,
                mode TEXT NOT NULL DEFAULT 'vertical',
                columns INTEGER NOT NULL DEFAULT 1,
                order_json TEXT,
                row_widths_json TEXT
            )""")
            try:
                cur.execute("ALTER TABLE ui_layouts ADD COLUMN row_widths_json TEXT")
            except Exception as exc:
                if "duplicate column" not in str(exc).lower():
                    raise
            cur.execute("""CREATE TABLE IF NOT EXISTS ui_custom_buttons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                screen_key TEXT NOT NULL,
                text TEXT NOT NULL,
                style TEXT NOT NULL DEFAULT 'primary',
                action_type TEXT NOT NULL,
                action_value TEXT NOT NULL,
                position INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )""")
            try:
                cur.execute("ALTER TABLE ui_custom_buttons ADD COLUMN custom_emoji_id TEXT")
            except Exception as exc:
                if "duplicate column" not in str(exc).lower():
                    raise

        _SCHEMA_READY = True


def _pattern_matches(pattern: str, callback_data: str) -> bool:
    return callback_data == pattern or (pattern.endswith("_") and callback_data.startswith(pattern))



_AUTO_REGISTRY_READY = False
_AUTO_REGISTRY: dict[str, dict[str, Any]] = {}
_AUTO_REGEX_CACHE: dict[str, re.Pattern] = {}
_AUTO_MATCH_ENTRIES: list[dict] | None = None

def _ast_message_template(node) -> tuple[str, list[str]] | None:
    """Convert a static string/f-string argument into an editable template.
    Dynamic expressions become stable {v1}, {v2} placeholders so the user can
    rewrite the sentence without losing runtime values."""
    import ast
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value, []
    if not isinstance(node, ast.JoinedStr):
        return None
    parts, names = [], []
    idx = 0
    for value in node.values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            parts.append(value.value)
        elif isinstance(value, ast.FormattedValue):
            idx += 1
            name = f"v{idx}"
            names.append(name)
            parts.append("{" + name + "}")
        else:
            return None
    return "".join(parts), names

def _discover_auto_messages() -> None:
    global _AUTO_REGISTRY_READY
    if _AUTO_REGISTRY_READY:
        return
    _AUTO_REGISTRY_READY = True
    try:
        import ast
        from pathlib import Path
        root = Path(__file__).resolve().parent
        targets = [root / "handlers", root / "utils.py", root / "alerts.py"]
        seen = set()
        for target in targets:
            files = list(target.rglob("*.py")) if target.is_dir() else [target]
            for path in files:
                if path.name.startswith("__"):
                    continue
                try:
                    tree = ast.parse(path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call):
                        continue
                    fn = getattr(node.func, "attr", "")
                    if fn not in {"answer", "send_message", "edit_text", "send_photo",
                                  "send_video", "send_document", "send_animation",
                                  "edit_caption", "send_sticker"}:
                        continue
                    candidates = list(node.args)
                    for kw in node.keywords:
                        if kw.arg in {"text", "caption"}:
                            candidates.append(kw.value)
                    for candidate in candidates:
                        result = _ast_message_template(candidate)
                        if not result:
                            continue
                        template, _names = result
                        template = template.strip()
                        if len(template) < 4 or template.startswith("http"):
                            continue
                        # Don't register obvious URLs, code fragments or keyboard labels.
                        if template in seen:
                            continue
                        seen.add(template)
                        import hashlib
                        key = "auto_" + hashlib.sha1(
                            f"{path.relative_to(root)}:{getattr(node, 'lineno', 0)}:{template}".encode("utf-8")
                        ).hexdigest()[:12]
                        category = "🧩 پیام‌های کشف‌شده"
                        _AUTO_REGISTRY[key] = {
                            "key": key, "template": template, "category": category,
                            "source": str(path.relative_to(root)),
                            "line": getattr(node, "lineno", 0),
                            "vars": _names,
                        }
    except Exception:
        logger.exception("auto message discovery failed")

def _auto_pattern(template: str) -> re.Pattern:
    cached = _AUTO_REGEX_CACHE.get(template)
    if cached:
        return cached
    import re as _re
    parts = []
    pos = 0
    for m in _re.finditer(r"\{([A-Za-z_][A-Za-z0-9_]*)(?::[^}]*)?\}", template):
        parts.append(_re.escape(template[pos:m.start()]))
        parts.append(r"(.*?)")
        pos = m.end()
    parts.append(_re.escape(template[pos:]))
    try:
        pattern = _re.compile("^" + "".join(parts) + "$", _re.S)
    except Exception:
        pattern = _re.compile("^" + _re.escape(template) + "$", _re.S)
    _AUTO_REGEX_CACHE[template] = pattern
    return pattern

def auto_entries_for_screen(key: str) -> list[dict]:
    """Return discoverable messages relevant to a screen plus catalog messages.
    It is intentionally built once and never scans source during each render."""
    _discover_auto_messages()
    screen = SCREENS.get(key) or {}
    cat = screen.get("category")
    # Map UI categories to catalog sections using keywords; unknown screens still
    # receive discovered messages from their source.
    keyword_map = {
        "start": ("شروع", "اعلان"),
        "shop": ("خرید", "پرداخت", "سرویس سفارشی"),
        "finance": ("کیف پول", "فاکتور", "پرداخت", "اعلان"),
        "services": ("سرویس", "تحویل", "تمدید"),
        "discount": ("تخفیف",),
        "referral": ("دعوت",),
        "guides": ("راهنما",),
        "support": ("پشتیبانی",),
        "agency": ("پشتیبانی و نمایندگی",),
        "renewal": ("تمدید",),
        "custom_service": ("سرویس سفارشی",),
    }
    wanted = keyword_map.get(cat, ())
    items = []
    try:
        import text_catalog as _tc
        for section, entries in _tc.TEXT_CATEGORIES.items():
            if wanted and not any(w in section for w in wanted):
                continue
            for k, template in entries:
                if _is_text_catalog_content_key(k):
                    items.append({"key": k, "template": template, "source": section})
    except Exception:
        pass
    # Add source-discovered messages; avoid duplicates with catalog exact templates.
    known = {(x["key"], x["template"]) for x in items}
    for item in _AUTO_REGISTRY.values():
        if (item["key"], item["template"]) not in known:
            items.append(item)
    # Stable order and de-dup by key.
    out, seen = [], set()
    for item in items:
        if item["key"] in seen:
            continue
        seen.add(item["key"])
        out.append(item)
    return out

def auto_text_entries() -> list[dict]:
    global _AUTO_MATCH_ENTRIES
    if _AUTO_MATCH_ENTRIES is not None:
        return _AUTO_MATCH_ENTRIES
    _discover_auto_messages()
    out = []
    try:
        import text_catalog as _tc
        for section, entries in _tc.TEXT_CATEGORIES.items():
            for k, template in entries:
                if _is_text_catalog_content_key(k):
                    out.append({"key": k, "template": template, "source": section})
    except Exception:
        pass
    out.extend(_AUTO_REGISTRY.values())
    seen = set()
    result = []
    for item in out:
        if item["key"] in seen:
            continue
        seen.add(item["key"])
        result.append(item)
    result.sort(key=lambda x: len(x.get("template", "")), reverse=True)
    _AUTO_MATCH_ENTRIES = result
    return result

def apply_auto_text_with_entities(text: str):
    """Apply the first matching catalog/source override without DB work when no
    override exists. Dynamic source messages retain runtime values through vN."""
    if not text:
        return text, []
    _discover_auto_messages()
    # Catalog first: exact template regex with named placeholders.
    candidates = auto_text_entries()
    for item in candidates:
        template = item.get("template", "")
        if not template:
            continue
        pattern = _auto_pattern(template)
        m = pattern.match(text)
        if not m:
            continue
        key = item["key"]
        override = get_text(key, template)
        if override == template and not get_entities(key):
            continue
        values = {}
        names = re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)(?::[^}]*)?\}", template)
        groups = m.groups()
        for idx, name in enumerate(names):
            if idx < len(groups):
                values[name] = groups[idx]
        rendered, entities = render_template(key, values, fallback=override)
        return rendered, entities
    return text, []



# 🐛 فیکس: یک callback مثل «plans» هم برای دکمه‌ی منوی اصلی «🛒 خرید اشتراک» و هم برای ده‌ها
# دکمه‌ی «🔙 بازگشت/انصراف» (که به همان صفحه برمی‌گردند) استفاده می‌شود. قبلاً همه‌ی آن‌ها یک
# رکورد مشترک داشتند، پس تغییر نام «خرید اشتراک» روی همه‌ی دکمه‌های بازگشت هم اعمال می‌شد.
# حالا دکمه‌ها بر اساس «نقش» هم گروه‌بندی می‌شوند: گروه back (بازگشت/انصراف/لغو) و گروه main
# (بقیه). فقط دکمه‌های هم‌گروه با هم مشترک‌اند.
_BACK_WORDS = ("بازگشت", "انصراف", "لغو", "🔙", "🏠", "⬅️")


def _label_group(label: str | None) -> str:
    text = str(label or "")
    return "back" if any(w in text for w in _BACK_WORDS) else "main"


def _button_group(screen_key: str | None, callback_key: str) -> str:
    """گروه (back/main) دکمه‌ی مشخص‌شده بر اساس برچسب پیش‌فرضش در رجیستری."""
    if screen_key and screen_key in SCREENS:
        for pattern, label in SCREENS[screen_key].get("buttons", []):
            if pattern == callback_key:
                return _label_group(label)
    return "main"


def _callback_occurrences(callback_key: str, group: str | None = None) -> list[tuple[str, str]]:
    """Return all screen/pattern locations for a logical callback (optionally only one group)."""
    result = []
    for screen_key, screen in SCREENS.items():
        for pattern, label in screen.get("buttons", []):
            if pattern == callback_key and (group is None or _label_group(label) == group):
                result.append((screen_key, pattern))
    return result


def _callback_group_names(callback_key: str) -> set[str]:
    return {_label_group(label) for screen in SCREENS.values()
            for pattern, label in screen.get("buttons", []) if pattern == callback_key}


def _group_storage_cb(callback_key: str, group: str) -> str:
    # اگر یک callback هر دو نقش را دارد، گروه back کلید جدا (cb#back) می‌گیرد و گروه main همان
    # کلید قبلی را نگه می‌دارد (بنابراین ویرایش‌های قبلیِ دکمه‌ی اصلی دست‌نخورده می‌ماند).
    if group == "back" and len(_callback_group_names(callback_key)) > 1:
        return f"{callback_key}#back"
    return callback_key


def _is_shared_button(callback_key: str, screen_key: str | None = None, group: str | None = None) -> bool:
    if group is None:
        if screen_key is not None:
            group = _button_group(screen_key, callback_key)
        else:
            return any(len(_callback_occurrences(callback_key, g)) > 1 for g in _callback_group_names(callback_key))
    return len(_callback_occurrences(callback_key, group)) > 1


def _button_storage_key(screen_key: str, callback_key: str) -> tuple[str, str]:
    # Shared logical buttons (same callback + same role) have one canonical DB record. This makes
    # e.g. «back» editable once and consistent everywhere, without touching the main-menu button.
    group = _button_group(screen_key, callback_key)
    if _is_shared_button(callback_key, group=group):
        return "__global__", _group_storage_cb(callback_key, group)
    return screen_key, callback_key

def get_screen(key: str):
    return SCREENS.get(key)


def category_label(key: str):
    return CATEGORIES.get(key, key)


def editor_mode_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ ویرایش متن", callback_data="ui_mode:text", style="primary")],
        [InlineKeyboardButton(text="🔘 ویرایش دکمه‌ها", callback_data="ui_mode:buttons", style="primary")],
    ])


def categories_keyboard(mode: str = "text"):
    _ensure()
    items = list(CATEGORIES.items())
    rows = []
    for i in range(0, len(items), 2):
        row = [InlineKeyboardButton(text=v, callback_data=f"ui_cat:{mode}:{k}", style="primary") for k, v in items[i:i + 2]]
        rows.append(row)
    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_text_editor", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


_TEXT_ONLY_EXCLUDED_PREFIXES = (
    "main_btn_",
    "btn_",
    "pay_",
    "wallet_pay_",
    "renew_pay_",
    "custom_pay_",
    "online_payment_",
    "invoice_copy_",
)
_TEXT_ONLY_EXCLUDED_KEYS = {
    "join_confirm",
    "insufficient_charge",
}


def _is_text_catalog_content_key(key: str) -> bool:
    if key in _TEXT_ONLY_EXCLUDED_KEYS:
        return False
    return not any(key.startswith(prefix) for prefix in _TEXT_ONLY_EXCLUDED_PREFIXES)


def text_catalog_entries(category: str):
    """فقط متن‌های واقعی ربات را برمی‌گرداند، نه برچسب دکمه‌ها."""
    if category == "🧩 پیام‌های کشف‌شده":
        _discover_auto_messages()
        return [(item["key"], item["template"]) for item in _AUTO_REGISTRY.values()]
    try:
        import text_catalog as _tc
        entries = list(_tc.TEXT_CATEGORIES.get(category, []))
    except Exception:
        entries = []
    return [(key, default) for key, default in entries if _is_text_catalog_content_key(key)]


def text_catalog_categories():
    """دسته‌هایی که بعد از حذف برچسب دکمه‌ها هنوز متن واقعی دارند."""
    try:
        import text_catalog as _tc
        cats = list(_tc.TEXT_CATEGORIES.keys())
    except Exception:
        cats = []
    cats = [cat for cat in cats if text_catalog_entries(cat)]
    _discover_auto_messages()
    if _AUTO_REGISTRY:
        cats.append("🧩 پیام‌های کشف‌شده")
    return cats


def text_catalog_find(key: str):
    if str(key).startswith("auto_"):
        _discover_auto_messages()
        item = _AUTO_REGISTRY.get(str(key))
        if item:
            return item["category"], item["template"]
    try:
        import text_catalog as _tc
        for cat in _tc.TEXT_CATEGORIES.keys():
            for k, default in text_catalog_entries(cat):
                if k == key:
                    return cat, default
    except Exception:
        pass
    return "", ""


def text_categories_keyboard():
    """کیبورد دسته‌بندی‌های متن از text_catalog"""
    try:
        import text_catalog as _tc
        cats = text_catalog_categories()
    except Exception:
        cats = []
    rows = []
    for i in range(0, len(cats), 2):
        row = []
        for cat in cats[i:i+2]:
            row.append(InlineKeyboardButton(
                text=cat,
                callback_data=f"uitc_cat:{cat[:40]}",
                style="primary"
            ))
        rows.append(row)
    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_text_editor", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def text_category_entries_keyboard(category: str, page: int = 0, page_size: int = 8):
    """کیبورد فهرست متن‌های یک دسته"""
    try:
        import text_catalog as _tc
        entries = text_catalog_entries(category)
    except Exception:
        entries = []
    total = len(entries)
    start = page * page_size
    page_entries = entries[start:start + page_size]
    rows = []
    for key, default_text in page_entries:
        current = get_text(key, default_text).replace('\n', ' ').strip()
        preview = current[:28] + ('…' if len(current) > 28 else '')
        rows.append([InlineKeyboardButton(
            text=f"✏️ {preview}",
            callback_data=f"uitc_key:{key}",
            style="primary"
        )])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ قبلی", callback_data=f"uitc_pg:{category[:30]}:{page-1}", style="primary"))
    if start + page_size < total:
        nav.append(InlineKeyboardButton(text="بعدی ➡️", callback_data=f"uitc_pg:{category[:30]}:{page+1}", style="primary"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="🔙 بازگشت به دسته‌بندی‌ها", callback_data="uitc_cats", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def text_entry_keyboard(key: str, category: str = ""):
    """کیبورد ویرایش یک متن"""
    rows = [
        [InlineKeyboardButton(text="✏️ ویرایش این متن", callback_data=f"uitc_edit:{key}", style="primary")],
    ]
    # بازنشانی به پیش‌فرض
    rows.append([InlineKeyboardButton(text="🔄 بازنشانی به پیش‌فرض", callback_data=f"uitc_reset:{key}", style="danger")])
    if category:
        rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"uitc_cat:{category[:40]}", style="danger")])
    else:
        rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="uitc_cats", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)



def all_button_entries(page: int = 0, page_size: int = 8):
    """فهرست جامع دکمه‌ها؛ callbackهای مشترک فقط یک‌بار نمایش داده می‌شوند."""
    _ensure()
    items = []
    seen_callbacks = set()
    for screen_key, screen in SCREENS.items():
        for cb, label in screen.get("buttons", []):
            _grp = _label_group(label)
            if _is_shared_button(cb, group=_grp):
                if (cb, _grp) in seen_callbacks:
                    continue
                seen_callbacks.add((cb, _grp))
                occurrences = _callback_occurrences(cb, _grp)
                screen_key = occurrences[0][0] if occurrences else screen_key
            else:
                ident = (screen_key, cb)
                if ident in seen_callbacks:
                    continue
                seen_callbacks.add(ident)
            items.append({
                "screen_key": screen_key,
                "callback_key": cb,
                "label": label,
                "screen_label": screen.get("label", screen_key),
                "shared": _is_shared_button(cb, group=_grp),
            })
    items.sort(key=lambda x: (not x.get("shared", False), x["screen_label"], x["callback_key"]))
    start = max(0, int(page)) * page_size
    return items[start:start + page_size], len(items)


def _button_in_bundle(item: dict, bundle_key: str) -> bool:
    bundle = BUTTON_BUNDLES.get(bundle_key) or {}
    if not bundle:
        return False
    screen_key = item.get("screen_key")
    callback_key = item.get("callback_key")
    if screen_key in set(bundle.get("screen_keys") or set()):
        return True
    if callback_key in set(bundle.get("callback_keys") or set()):
        return True
    for prefix in bundle.get("callback_prefixes") or ():
        if callback_key == prefix or str(callback_key).startswith(prefix):
            return True
    return False


def button_bundle_entries(bundle_key: str):
    items, _total = all_button_entries(0, 10000)
    return [item for item in items if _button_in_bundle(item, bundle_key)]


def button_bundles_keyboard():
    rows = []
    for key, meta in BUTTON_BUNDLES.items():
        count = len(button_bundle_entries(key))
        rows.append([InlineKeyboardButton(text=f"{meta['label']} ({count})", callback_data=f"ui_btnbundle:{key}", style="primary")])
    rows.append([InlineKeyboardButton(text="🧾 همه‌ی دکمه‌ها", callback_data="ui_btnbundle:all", style="success")])
    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_text_editor", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def button_bundle_keyboard(bundle_key: str, page: int = 0, page_size: int = 8):
    if bundle_key == "all":
        items, total = all_button_entries(page, page_size)
        bundle_label = "همه‌ی دکمه‌ها"
    else:
        all_items = button_bundle_entries(bundle_key)
        total = len(all_items)
        start = max(0, int(page)) * page_size
        items = all_items[start:start + page_size]
        bundle_label = (BUTTON_BUNDLES.get(bundle_key) or {}).get("label", bundle_key)

    rows = []
    for item in items:
        label = get_button(item["screen_key"], item["callback_key"], item["label"])
        meta = get_button_meta(item["screen_key"], item["callback_key"])
        hidden_mark = "🚫" if meta["hidden"] else "👁"
        screen_text = item["screen_label"]
        short = label if len(label) <= 20 else label[:17] + "…"
        rows.append([
            InlineKeyboardButton(text=f"✏️ {short}", callback_data=f"ui_button:{item['screen_key']}:{item['callback_key']}", style="primary"),
            InlineKeyboardButton(text=hidden_mark, callback_data=f"ui_toggle:{item['screen_key']}:{item['callback_key']}", style="primary"),
            InlineKeyboardButton(text=_style_dot(meta.get("style")), callback_data=f"ui_style:{item['screen_key']}:{item['callback_key']}", style="primary"),
            InlineKeyboardButton(text="↕️", callback_data=f"ui_screen:buttons:{item['screen_key']}", style="primary"),
        ])
        rows.append([InlineKeyboardButton(text=f"📂 {screen_text}", callback_data=f"ui_screen:buttons:{item['screen_key']}", style="primary")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ قبلی", callback_data=f"ui_bundle_page:{bundle_key}:{page-1}", style="primary"))
    if (page + 1) * page_size < total:
        nav.append(InlineKeyboardButton(text="بعدی ➡️", callback_data=f"ui_bundle_page:{bundle_key}:{page+1}", style="primary"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text=f"🔙 بازگشت به بسته‌ها ({bundle_label})", callback_data="ui_mode:buttons", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def all_buttons_keyboard(page: int = 0, page_size: int = 8):
    return button_bundle_keyboard("all", page, page_size)


def screens_keyboard(cat: str, mode: str = "text"):
    _ensure()
    if cat == "all_messages" and mode == "text":
        return text_categories_keyboard()
    if cat == "all_buttons" and mode == "buttons":
        return all_buttons_keyboard(0)
    rows = []
    for key, screen in SCREENS.items():
        if screen.get("category") != cat:
            continue
        # فیلتر: اگر نه default text دارد نه button، نشان نده
        has_text = bool((screen.get("default") or "").strip())
        has_buttons = bool(screen.get("buttons"))
        if mode == "text" and not has_text:
            continue
        if mode == "buttons" and not has_buttons:
            continue
        rows.append([InlineKeyboardButton(text=screen["label"], callback_data=f"ui_screen:{mode}:{key}", style="primary")])
    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="ui_mode:" + mode, style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_text(key: str, fallback: str | None = None) -> str:
    _ensure()
    # perf: پرتکرارترین تابع کل ربات — تقریباً هر متنی که به کاربر نشون داده
    # می‌شه از همینجا رد می‌شه؛ کش کردنش بیشترین تاثیر رو روی سرعت پاسخ‌گویی داره.
    cache_key = f"uitext:{key}"
    cached_value, hit = cache.get(cache_key)
    if hit:
        return cached_value if cached_value is not None else (fallback if fallback is not None else SCREENS.get(key, {}).get("default", ""))
    cur = db.get_connection().cursor()
    cur.execute("SELECT text FROM ui_text_overrides WHERE key=?", (key,))
    row = cur.fetchone()
    if row:
        cache.set(cache_key, row[0])
        return row[0]
    # Overrides saved by older Editor versions used a hash of the template only.
    # Keep them readable after the new source-specific catalog is deployed.

    cache.set(cache_key, None)
    if fallback is not None and fallback != "":
        return fallback
    auto_default = None
    if str(key).startswith("auto_"):
        _discover_auto_messages()
        auto_default = (_AUTO_REGISTRY.get(str(key)) or {}).get("template")
    if auto_default is not None:
        return auto_default
    return fallback if fallback is not None else SCREENS.get(key, {}).get("default", "")


def get_alert_text(key: str, fallback: str = "") -> str:
    """متن کوتاه Callback Alert/Toast را از همان مخزن ویرایش متن می‌خواند."""
    return get_text(key, fallback)


def get_entities(key: str):
    _ensure()
    cache_key = f"uientities:{key}"
    cached_value, hit = cache.get(cache_key)
    if hit:
        return cached_value
    cur = db.get_connection().cursor()
    cur.execute("SELECT entities_json FROM ui_text_overrides WHERE key=?", (key,))
    row = cur.fetchone()
    if not row or not row[0]:
        cache.set(cache_key, [])
        return []
    try:
        value = json.loads(row[0])
    except Exception:
        value = []
    cache.set(cache_key, value)
    return value



def _utf16_to_py_index(text: str, offset_units: int) -> int:
    units = 0
    for i, ch in enumerate(text):
        if units >= offset_units:
            return i
        units += 2 if ord(ch) > 0xFFFF else 1
    return len(text)


def _py_to_utf16_offset(text: str, index: int) -> int:
    return sum(2 if ord(ch) > 0xFFFF else 1 for ch in text[:index])


def _sanitize_config_detail_template(template: str, entities: list[dict]):
    """دو فیلد بلااستفاده‌ی قدیمی جزئیات سرویس را حذف می‌کند و آفست Custom Emojiها را سالم نگه می‌دارد."""
    if not template:
        return template, entities

    blocked_labels = (
        "🔃 آخرین زمان آپدیت لینک اشتراک",
        "🌍 کلاینت متصل شده",
    )
    removed = []
    pieces = []
    cursor = 0
    pos = 0
    for line in template.splitlines(keepends=True):
        line_start = pos
        line_end = pos + len(line)
        pos = line_end
        if any(label in line for label in blocked_labels):
            removed.append((line_start, line_end))
            continue
        pieces.append(line)
    if not removed:
        return template, entities

    sanitized = "".join(pieces)
    remapped = []
    for entity in entities or []:
        try:
            old_start = _utf16_to_py_index(template, int(entity.get("offset", 0)))
            old_end = _utf16_to_py_index(template, int(entity.get("offset", 0)) + int(entity.get("length", 0)))
        except Exception:
            continue
        if any(old_start < end and old_end > start for start, end in removed):
            continue
        shift = sum(end - start for start, end in removed if end <= old_start)
        item = dict(entity)
        new_start = max(0, old_start - shift)
        new_end = max(new_start, old_end - shift)
        item["offset"] = _py_to_utf16_offset(sanitized, new_start)
        item["length"] = _py_to_utf16_offset(sanitized, new_end) - item["offset"]
        remapped.append(item)
    return sanitized, remapped


def _fmt_value(values: dict, match) -> str:
    """مقدار placeholder را با قالب اختیاری ({amount:,} و …) برمی‌گرداند."""
    name = match.group(1)
    if name not in values:
        return match.group(0)
    value = values[name]
    spec_match = re.match(r"\{[A-Za-z_][A-Za-z0-9_]*:([^}]*)\}", match.group(0))
    if spec_match:
        try:
            return format(value, spec_match.group(1))
        except Exception:
            pass
    return str(value)


def render_template(key: str, values: dict[str, Any], fallback: str | None = None):
    """متن ذخیره‌شده را با placeholderها رندر می‌کند و Custom Emojiها را حفظ می‌کند."""
    template = get_text(key, fallback)
    entities = get_entities(key)
    if key == "config_detail":
        template, entities = _sanitize_config_detail_template(template, entities)
    if not values:
        return template, entities

    # ساخت متن جدید و نگاشت محدوده‌های متن قدیمی به متن جدید.
    parts = []
    cursor = 0
    mapping_segments = []  # (old_start, old_end, new_start, new_end)
    import re
    pattern = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)(?::[^}]*)?\}")
    for match in pattern.finditer(template):
        parts.append(template[cursor:match.start()])
        new_start = sum(len(x) for x in parts)
        replacement = _fmt_value(values, match)
        parts.append(replacement)
        new_end = sum(len(x) for x in parts)
        # خود placeholder عمداً map نمی‌شود؛ اگر کسی Custom Emoji را داخل آن گذاشته باشد،
        # بهتر است آن entity حذف شود تا offset خراب به تلگرام ارسال نشود.
        mapping_segments.append((cursor, match.start(), sum(len(x) for x in parts[:-2]), new_start))
        cursor = match.end()
    parts.append(template[cursor:])
    rendered = "".join(parts)

    # entityها را بر اساس تعداد کاراکترهای قبل از هر placeholder جابه‌جا می‌کنیم.
    remapped = []
    for entity in entities:
        try:
            old_start = _utf16_to_py_index(template, int(entity.get("offset", 0)))
            old_end = _utf16_to_py_index(template, int(entity.get("offset", 0)) + int(entity.get("length", 0)))
        except Exception:
            continue
        delta = 0
        overlaps = False
        for match in pattern.finditer(template):
            if old_end <= match.start():
                break
            if old_start >= match.end():
                delta += len(_fmt_value(values, match)) - (match.end() - match.start())
            else:
                overlaps = True
                break
        if overlaps:
            continue
        new_start = old_start + delta
        new_end = old_end + delta
        item = dict(entity)
        item["offset"] = _py_to_utf16_offset(rendered, new_start)
        item["length"] = _py_to_utf16_offset(rendered, new_end) - item["offset"]
        remapped.append(item)
    return rendered, remapped

def set_text(key: str, text: str, entities=None):
    _ensure()
    with db.transaction() as cur:
        cur.execute(
            "INSERT INTO ui_text_overrides(key,text,entities_json,updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET text=excluded.text,entities_json=excluded.entities_json,updated_at=excluded.updated_at",
            (key, text, json.dumps(entities or [], ensure_ascii=False), datetime.datetime.now().isoformat()),
        )
    cache.invalidate(f"uitext:{key}")
    cache.invalidate(f"uientities:{key}")
    # Auto-rendered messages may contain this override.
    cache.invalidate_prefix("uiauto:")


def get_button(key: str, callback_key: str, default: str) -> str:
    _ensure()
    gkey, gcb = _button_storage_key(key, callback_key)
    cache_key = f"uibtn:{gkey}:{gcb}"
    cached_value, hit = cache.get(cache_key)
    if hit:
        return cached_value if cached_value is not None else default
    cur = db.get_connection().cursor()
    if gkey == "__global__":
        cur.execute("SELECT text FROM ui_button_overrides WHERE screen_key=? AND callback_key=?", (gkey, gcb))
        row = cur.fetchone()
        if not row:
            # Backward compatibility: prefer the first existing per-screen override (same role only).
            for sk, cb in _callback_occurrences(callback_key, _button_group(key, callback_key)):
                cur.execute("SELECT text FROM ui_button_overrides WHERE screen_key=? AND callback_key=?", (sk, cb))
                row = cur.fetchone()
                if row:
                    break
    else:
        cur.execute("SELECT text FROM ui_button_overrides WHERE screen_key=? AND callback_key=?", (gkey, gcb))
        row = cur.fetchone()
    cache.set(cache_key, row[0] if row else None)
    return row[0] if row else default


def set_button(key: str, callback_key: str, text: str, custom_emoji_id: str | None = None):
    _ensure()
    gkey, gcb = _button_storage_key(key, callback_key)
    with db.transaction() as cur:
        cur.execute(
            "INSERT INTO ui_button_overrides(screen_key,callback_key,text) VALUES(?,?,?) "
            "ON CONFLICT(screen_key,callback_key) DO UPDATE SET text=excluded.text",
            (gkey, gcb, text),
        )
        cur.execute(
            "INSERT INTO ui_button_meta(screen_key,callback_key,custom_emoji_id,hidden) VALUES(?,?,?,COALESCE((SELECT hidden FROM ui_button_meta WHERE screen_key=? AND callback_key=?),0)) "
            "ON CONFLICT(screen_key,callback_key) DO UPDATE SET custom_emoji_id=excluded.custom_emoji_id",
            (gkey, gcb, custom_emoji_id, gkey, gcb),
        )
        # Remove stale per-screen overrides after a shared button is centralized.
        if gkey == "__global__":
            for sk, cb in _callback_occurrences(callback_key, _button_group(key, callback_key)):
                cur.execute("DELETE FROM ui_button_overrides WHERE screen_key=? AND callback_key=?", (sk, cb))
                cur.execute("DELETE FROM ui_button_meta WHERE screen_key=? AND callback_key=?", (sk, cb))
    cache.invalidate_prefix("uibtn:")
    cache.invalidate_prefix("uibtnmeta:")


def get_button_meta(key: str, callback_key: str) -> dict:
    _ensure()
    gkey, gcb = _button_storage_key(key, callback_key)
    cache_key = f"uibtnmeta:{gkey}:{gcb}"
    cached_value, hit = cache.get(cache_key)
    if hit:
        return cached_value
    cur = db.get_connection().cursor()
    cur.execute("SELECT custom_emoji_id,hidden,style FROM ui_button_meta WHERE screen_key=? AND callback_key=?", (gkey, gcb))
    row = cur.fetchone()
    if not row and gkey == "__global__":
        for sk, cb in _callback_occurrences(callback_key, _button_group(key, callback_key)):
            cur.execute("SELECT custom_emoji_id,hidden,style FROM ui_button_meta WHERE screen_key=? AND callback_key=?", (sk, cb))
            row = cur.fetchone()
            if row:
                break
    value = {"custom_emoji_id": row[0] if row else None, "hidden": bool(row[1]) if row else False, "style": row[2] if row else None}
    cache.set(cache_key, value)
    return value


_VALID_BUTTON_STYLES = {"primary", "secondary", "success", "danger"}
_STYLE_CYCLE = [None, "primary", "success", "danger"]
_STYLE_DOTS = {None: "⚪️", "primary": "🔵", "success": "🟢", "danger": "🔴"}


def _style_dot(style: str | None) -> str:
    return _STYLE_DOTS.get(style, "⚪️")


def set_button_style(key: str, callback_key: str, style: str | None):
    if style is not None and style not in _VALID_BUTTON_STYLES:
        raise ValueError("رنگ دکمه نامعتبر است")
    _ensure()
    gkey, gcb = _button_storage_key(key, callback_key)
    with db.transaction() as cur:
        cur.execute(
            "INSERT INTO ui_button_meta(screen_key,callback_key,custom_emoji_id,hidden,style) "
            "VALUES(?,?,NULL,COALESCE((SELECT hidden FROM ui_button_meta WHERE screen_key=? AND callback_key=?),0),?) "
            "ON CONFLICT(screen_key,callback_key) DO UPDATE SET style=excluded.style",
            (gkey, gcb, gkey, gcb, style),
        )
    cache.invalidate_prefix("uibtnmeta:")


def toggle_button(key: str, callback_key: str):
    _ensure()
    gkey, gcb = _button_storage_key(key, callback_key)
    old = get_button_meta(key, callback_key)["hidden"]
    with db.transaction() as cur:
        cur.execute(
            "INSERT INTO ui_button_meta(screen_key,callback_key,custom_emoji_id,hidden,style) "
            "VALUES(?,?,NULL,?,NULL) ON CONFLICT(screen_key,callback_key) DO UPDATE SET hidden=excluded.hidden",
            (gkey, gcb, 0 if old else 1),
        )
    cache.invalidate_prefix("uibtnmeta:")


def reset_button(key: str, callback_key: str):
    _ensure()
    gkey, gcb = _button_storage_key(key, callback_key)
    with db.transaction() as cur:
        cur.execute("DELETE FROM ui_button_overrides WHERE screen_key=? AND callback_key=?", (gkey, gcb))
        cur.execute("DELETE FROM ui_button_meta WHERE screen_key=? AND callback_key=?", (gkey, gcb))
    cache.invalidate_prefix("uibtn:")
    cache.invalidate_prefix("uibtnmeta:")

def next_button_style(current: str | None) -> str | None:
    """چرخه‌ی یک‌کلیکی رنگ: ⚪️(پیش‌فرض) → 🔵(آبی) → 🟢(سبز) → 🔴(قرمز) → ⚪️...
    طبق Bot API 9.4 فقط همین سه رنگ واقعاً روی تلگرام دیده می‌شوند؛ ⚪️ یعنی
    بدون override (رنگ پیش‌فرض اپ کاربر)."""
    try:
        idx = _STYLE_CYCLE.index(current)
    except ValueError:
        idx = 0
    return _STYLE_CYCLE[(idx + 1) % len(_STYLE_CYCLE)]


def get_layout(key: str):
    _ensure()
    cache_key = f"uilayout:{key}"
    cached_value, hit = cache.get(cache_key)
    if hit:
        return cached_value
    cur = db.get_connection().cursor()
    cur.execute("SELECT mode,columns,order_json,row_widths_json FROM ui_layouts WHERE screen_key=?", (key,))
    row = cur.fetchone()
    if not row:
        # پیش‌فرض همه منوها یک دکمه در هر ردیف است؛ ادمین می‌تواند
        # از Editor چیدمان ۲/۳/۴تایی را انتخاب و ذخیره کند.
        value = {"mode": "vertical", "columns": 1, "order": None, "row_widths": None}
        cache.set(cache_key, value)
        return value
    try:
        order = json.loads(row[2]) if row[2] else None
    except Exception:
        order = None
    mode = row[0] if row[0] in ("vertical", "inline") else "vertical"
    columns = int(row[1] or 1)
    if mode == "vertical":
        columns = 1
    else:
        columns = min(4, max(1, columns))
    try:
        row_widths = json.loads(row[3]) if row[3] else None
        if not isinstance(row_widths, list):
            row_widths = None
    except Exception:
        row_widths = None
    value = {"mode": mode, "columns": columns, "order": order, "row_widths": row_widths}
    cache.set(cache_key, value)
    return value


def set_layout(key: str, mode: str, columns: int = 1):
    if mode not in ("vertical", "inline"):
        mode = "vertical"
    columns = 1 if mode == "vertical" else min(4, max(1, int(columns)))
    # تعداد دکمه‌ها لازم نیست مضرب columns باشد؛ ردیف آخر می‌تواند ناقص باشد.
    # مثال: 7 دکمه با columns=2 => 2+2+2+1.
    _ensure()
    with db.transaction() as cur:
        cur.execute(
            "INSERT INTO ui_layouts(screen_key,mode,columns,order_json) VALUES(?,?,?,NULL) "
            "ON CONFLICT(screen_key) DO UPDATE SET mode=excluded.mode,columns=excluded.columns",
            (key, mode, columns),
        )
    cache.invalidate(f"uilayout:{key}")


def set_row_layout(key: str, widths: list[int] | None):
    _ensure()
    clean=[max(1,min(8,int(x))) for x in (widths or []) if int(x)>0]
    with db.transaction() as cur:
        cur.execute(
            "INSERT INTO ui_layouts(screen_key,mode,columns,order_json,row_widths_json) VALUES(?,?,?,NULL,?) "
            "ON CONFLICT(screen_key) DO UPDATE SET row_widths_json=excluded.row_widths_json",
            (key, "inline" if clean else "vertical", max(clean or [1]), json.dumps(clean, ensure_ascii=False) if clean else None),
        )
    cache.invalidate(f"uilayout:{key}")


def _base_order(key: str):
    return [cb for cb, _ in SCREENS.get(key, {}).get("buttons", [])]


def get_order(key: str):
    base = _base_order(key)
    order = get_layout(key).get("order") or base[:]
    # حذف callbackهای قدیمی/نامعتبر و اضافه‌کردن callbackهای جدید به انتهای لیست.
    order = [x for x in order if x in base]
    order += [x for x in base if x not in order]
    return order


def move_button(key: str, callback_key: str, direction: str):
    order = get_order(key)
    if callback_key not in order:
        return
    i = order.index(callback_key)
    j = i - 1 if direction == "up" else i + 1
    if j < 0 or j >= len(order):
        return
    order[i], order[j] = order[j], order[i]
    lay = get_layout(key)
    _ensure()
    with db.transaction() as cur:
        cur.execute(
            "INSERT INTO ui_layouts(screen_key,mode,columns,order_json) VALUES(?,?,?,?) "
            "ON CONFLICT(screen_key) DO UPDATE SET order_json=excluded.order_json",
            (key, lay["mode"], lay["columns"], json.dumps(order, ensure_ascii=False)),
        )
    cache.invalidate(f"uilayout:{key}")


def _button_default(key: str, callback_key: str) -> str:
    for cb, label in SCREENS.get(key, {}).get("buttons", []):
        if cb == callback_key:
            return label
    return "🔘 دکمه"


def override_button_text(callback_data: str, default: str) -> str:
    """برای سازگاری با تمام محل‌های قدیمی که screen_key را نمی‌فرستند.

    🐛 فیکس: فقط دکمه‌هایی هم‌نقش با `default` در نظر گرفته می‌شوند. یعنی «🔙 بازگشت» که callbackش
    plans است هرگز override دکمه‌ی «🛒 خرید اشتراک» را نمی‌گیرد (و برعکس).
    """
    grp = _label_group(default)
    matches = []
    for key, screen in SCREENS.items():
        for cb, label in screen.get("buttons", []):
            if _pattern_matches(cb, callback_data) and _label_group(label) == grp:
                matches.append((key, cb, label))
    if len(matches) == 1:
        key, cb, label = matches[0]
        return get_button(key, cb, default)
    # بعضی helperهای قدیمی مثل back_button بدون screen_key ساخته می‌شوند.
    # اگر فقط یک override واقعی برای این callback (در همین نقش) ثبت شده باشد، همان را اعمال کن؛
    # در غیر این صورت برای جلوگیری از تغییر ناخواسته‌ی چند صفحه، به default برگرد.
    overridden = set()
    for key, cb, label in matches:
        value = get_button(key, cb, default)
        if value != default:
            overridden.add(value)
    if len(overridden) == 1:
        return next(iter(overridden))
    return default


def button_meta_for_callback(callback_data: str, default: str | None = None):
    grp = _label_group(default) if default is not None else None
    matches = []
    for key, screen in SCREENS.items():
        for cb, label in screen.get("buttons", []):
            if _pattern_matches(cb, callback_data) and (grp is None or _label_group(label) == grp):
                matches.append((key, cb))
    if not matches:
        return None
    if len(matches) > 1:
        cb = matches[0][1]
        if _is_shared_button(cb, screen_key=matches[0][0]):
            meta = get_button_meta(matches[0][0], cb)
            meta.update({"screen_key": "__global__", "callback_key": cb})
            return meta
        return None
    key, cb = matches[0]
    meta = get_button_meta(key, cb)
    meta.update({"screen_key": key, "callback_key": cb})
    return meta


def preview_text(key: str, page: int = 0) -> str:
    base = get_text(key, SCREENS[key].get("default", ""))
    variants = auto_entries_for_screen(key)
    page_size = 6
    start = max(0, int(page)) * page_size
    visible = variants[start:start + page_size]
    chunks = [f"📝 پیش‌نمایش «{SCREENS[key]['label']}»", "", base]
    if variants:
        chunks.extend(["", f"🧩 متن‌های واقعی این مسیر — صفحه {page + 1}/{max(1, (len(variants)+page_size-1)//page_size)}"])
        for i, item in enumerate(visible, start + 1):
            preview = get_text(item["key"], item["template"])
            chunks.extend(["", f"🧩 متن واقعی {i} — {item['source']}", preview])
    result = "\n".join(chunks)
    return result[:3900] if len(result) > 3900 else result


def text_screen_keyboard(key: str, page: int = 0):
    rows = [[InlineKeyboardButton(text="✏️ تغییر متن اصلی", callback_data=f"ui_edit_text:{key}", style="primary")]]
    variants = auto_entries_for_screen(key)
    page_size = 6
    start = max(0, int(page)) * page_size
    visible = variants[start:start + page_size]
    for i, item in enumerate(visible, start + 1):
        current = get_text(item["key"], item["template"]).replace("\n", " ").strip()
        preview = current[:30] + ("…" if len(current) > 30 else "")
        rows.append([InlineKeyboardButton(text=f"🧩 متن {i}: {preview}"[:48], callback_data=f"ui_edit_auto:{item['key']}", style="primary")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ قبلی", callback_data=f"ui_screen_textpage:{key}:{page-1}", style="primary"))
    if (page + 1) * page_size < len(variants):
        nav.append(InlineKeyboardButton(text="بعدی ➡️", callback_data=f"ui_screen_textpage:{key}:{page+1}", style="primary"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="🔙 بازگشت به فهرست متن‌ها", callback_data="ui_mode:text", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def button_screen_keyboard(key: str, page: int = 0):
    sc = SCREENS[key]
    lay = get_layout(key)
    order = get_order(key)
    labels = dict(sc.get("buttons") or {})
    page_size = 8
    start = max(0, int(page)) * page_size
    visible_order = order[start:start + page_size]
    rows = []
    for cb in visible_order:
        if cb not in labels:
            continue
        meta = get_button_meta(key, cb)
        status = "🚫" if meta["hidden"] else "👁"
        # UX: "noop" یک دکمه‌ی تزئینی/جداکننده است (مثلاً خط‌چین بین دو بخش
        # از منو) و هیچ عملکردی هنگام کلیک نداره. قبلاً دقیقاً مثل بقیه‌ی
        # دکمه‌های واقعی نمایش داده می‌شد و ادمین گمان می‌کرد این یه دکمه‌ی
        # خراب/بی‌فایده‌ست؛ الان با برچسب مشخص می‌شه که این فقط یه جداکننده‌ی
        # ظاهریه (هنوز هم می‌شه متن/ایموجیش رو عوض کرد، فقط کلیک روش کاری
        # انجام نمی‌ده).
        if cb == "noop":
            rows.append([
                InlineKeyboardButton(text=f"🏷 (جداکننده‌ی تزئینی) {get_button(key, cb, labels[cb])}"[:60], callback_data=f"ui_button:{key}:{cb}", style="primary"),
                InlineKeyboardButton(text=status, callback_data=f"ui_toggle:{key}:{cb}", style="primary"),
            ])
            continue
        rows.append([
            InlineKeyboardButton(text=f"✏️ {get_button(key, cb, labels[cb])}"[:60], callback_data=f"ui_button:{key}:{cb}", style="primary"),
            InlineKeyboardButton(text=status, callback_data=f"ui_toggle:{key}:{cb}", style="primary"),
            InlineKeyboardButton(text=_style_dot(meta.get("style")), callback_data=f"ui_style:{key}:{cb}", style="primary"),
            InlineKeyboardButton(text="♻️", callback_data=f"ui_reset:{key}:{cb}", style="danger"),
            InlineKeyboardButton(text="⬆️", callback_data=f"ui_move:{key}:{cb}:up", style="primary"),
            InlineKeyboardButton(text="⬇️", callback_data=f"ui_move:{key}:{cb}:down", style="primary"),
        ])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ قبلی", callback_data=f"ui_button_page:{key}:{page-1}", style="primary"))
    if (page + 1) * page_size < len(order):
        nav.append(InlineKeyboardButton(text="بعدی ➡️", callback_data=f"ui_button_page:{key}:{page+1}", style="primary"))
    if nav:
        rows.append(nav)

    current_cols = min(4, max(1, int(lay.get("columns") or 1)))
    layout_row = []
    for cols in (1, 2, 3, 4):
        mark = "✅ " if cols == current_cols else ""
        layout_row.append(InlineKeyboardButton(
            text=f"{mark}{cols} دکمه/ردیف",
            callback_data=f"ui_layout:{key}:set:{cols}",
         style="primary"))
    rows.append(layout_row)
    rows.append([InlineKeyboardButton(
        text=f"📐 چیدمان فعلی: {current_cols} دکمه در هر ردیف",
        callback_data=f"ui_layout:{key}:cycle:{1 if current_cols >= 4 else current_cols + 1}",
     style="primary")])
    current_rows = ",".join(str(x) for x in (lay.get("row_widths") or [])) or "پیش‌فرض"
    rows.append([InlineKeyboardButton(text=f"🧩 الگوی ردیف‌ها: {current_rows}", callback_data=f"ui_rowlayout:{key}", style="primary")])
    custom_count = len(get_custom_buttons(key))
    rows.append([InlineKeyboardButton(
        text=f"➕ دکمه‌های سفارشی این صفحه ({custom_count})",
        callback_data=f"ui_cbtn_list:{key}",
     style="primary")])
    rows.append([InlineKeyboardButton(text="🔙 بازگشت به فهرست دکمه‌ها", callback_data="ui_mode:buttons", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------------------------------------------------------------------------
# دکمه‌های سفارشی ادمین (افزودن دکمه‌ی دلخواه به هر صفحه)
# ---------------------------------------------------------------------------

BUTTON_STYLES = {
    "primary": "🔵 آبی (پیش‌فرض)",
    "success": "🟢 سبز",
    "danger": "🔴 قرمز",
}

# مقصدهای پرتکرار داخل ربات که ادمین می‌تواند بدون دونستن callback خام،
# دکمه‌ی سفارشی‌اش را مستقیم به آن‌ها وصل کند (مثلاً «راهنما» یا «پشتیبانی»).
QUICK_DESTINATIONS = [
    ("plans", "🛒 خرید اشتراک"),
    ("my_configs", "📱 سرویس‌های من"),
    ("wallet", "💰 کیف پول"),
    ("referral", "👥 دعوت دوستان"),
    ("profile", "👤 پروفایل من"),
    ("support", "👨‍💻 پشتیبانی"),
    ("user_guides", "📚 فهرست راهنماها"),
    ("agency", "🤝 درخواست نمایندگی"),
    ("back", "🏠 منوی اصلی"),
]


def get_custom_buttons(screen_key: str):
    _ensure()
    cache_key = f"uicbtn:{screen_key}"
    cached_value, hit = cache.get(cache_key)
    if hit:
        return cached_value
    cur = db.get_connection().cursor()
    cur.execute(
        "SELECT id,screen_key,text,style,action_type,action_value,position,custom_emoji_id FROM ui_custom_buttons "
        "WHERE screen_key=? ORDER BY position ASC, id ASC",
        (screen_key,),
    )
    rows = cur.fetchall()
    keys = ["id", "screen_key", "text", "style", "action_type", "action_value", "position", "custom_emoji_id"]
    value = [dict(zip(keys, r)) for r in rows]
    cache.set(cache_key, value)
    return value


def get_custom_button(button_id: int):
    _ensure()
    cur = db.get_connection().cursor()
    cur.execute(
        "SELECT id,screen_key,text,style,action_type,action_value,position,custom_emoji_id FROM ui_custom_buttons WHERE id=?",
        (button_id,),
    )
    row = cur.fetchone()
    if not row:
        return None
    keys = ["id", "screen_key", "text", "style", "action_type", "action_value", "position", "custom_emoji_id"]
    return dict(zip(keys, row))


def add_custom_button(screen_key: str, text: str, style: str, action_type: str, action_value: str, custom_emoji_id: str | None = None) -> int:
    _ensure()
    if style not in BUTTON_STYLES:
        style = "primary"
    with db.transaction() as cur:
        cur.execute("SELECT COALESCE(MAX(position),-1)+1 FROM ui_custom_buttons WHERE screen_key=?", (screen_key,))
        next_pos = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO ui_custom_buttons(screen_key,text,style,action_type,action_value,position,created_at,custom_emoji_id) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (screen_key, text, style, action_type, action_value, next_pos, datetime.datetime.now().isoformat(), custom_emoji_id),
        )
        new_id = cur.lastrowid
    cache.invalidate(f"uicbtn:{screen_key}")
    return new_id


def delete_custom_button(button_id: int):
    _ensure()
    btn = get_custom_button(button_id)
    with db.transaction() as cur:
        cur.execute("DELETE FROM ui_custom_buttons WHERE id=?", (button_id,))
    if btn:
        cache.invalidate(f"uicbtn:{btn['screen_key']}")


def move_custom_button(button_id: int, direction: str):
    btn = get_custom_button(button_id)
    if not btn:
        return
    siblings = get_custom_buttons(btn["screen_key"])
    idx = next((i for i, b in enumerate(siblings) if b["id"] == button_id), None)
    if idx is None:
        return
    j = idx - 1 if direction == "up" else idx + 1
    if j < 0 or j >= len(siblings):
        return
    a, b = siblings[idx], siblings[j]
    with db.transaction() as cur:
        cur.execute("UPDATE ui_custom_buttons SET position=? WHERE id=?", (b["position"], a["id"]))
        cur.execute("UPDATE ui_custom_buttons SET position=? WHERE id=?", (a["position"], b["id"]))
    cache.invalidate(f"uicbtn:{btn['screen_key']}")


def custom_buttons_keyboard(screen_key: str):
    items = get_custom_buttons(screen_key)
    rows = []
    for b in items:
        kind = {"callback": "🔗", "url": "🌐", "webapp": "📲"}.get(b["action_type"], "🔘")
        rows.append([
            InlineKeyboardButton(text=f"{kind} {b['text']}"[:40], callback_data=f"ui_cbtn_open:{b['id']}", style="primary",
                                  icon_custom_emoji_id=b.get("custom_emoji_id") or None),
            InlineKeyboardButton(text="⬆️", callback_data=f"ui_cbtn_move:{b['id']}:up", style="primary"),
            InlineKeyboardButton(text="⬇️", callback_data=f"ui_cbtn_move:{b['id']}:down", style="primary"),
            InlineKeyboardButton(text="🗑", callback_data=f"ui_cbtn_del:{b['id']}", style="primary"),
        ])
    rows.append([InlineKeyboardButton(text="➕ افزودن دکمه‌ی جدید", callback_data=f"ui_cbtn_add:{screen_key}", style="success")])
    rows.append([InlineKeyboardButton(text="🔙 بازگشت به تنظیمات دکمه‌های این صفحه", callback_data=f"ui_screen:buttons:{screen_key}", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def custom_button_style_keyboard(screen_key: str):
    rows = [[InlineKeyboardButton(text=v, callback_data=f"ui_cbtn_style:{screen_key}:{k}", style="primary")] for k, v in BUTTON_STYLES.items()]
    rows.append([InlineKeyboardButton(text="🔙 انصراف", callback_data=f"ui_cbtn_list:{screen_key}", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def custom_button_action_type_keyboard(screen_key: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 وصل به یکی از صفحات ربات", callback_data=f"ui_cbtn_type:{screen_key}:callback_quick", style="success")],
        [InlineKeyboardButton(text="✍️ وارد کردن callback دستی (پیشرفته)", callback_data=f"ui_cbtn_type:{screen_key}:callback_manual", style="danger")],
        [InlineKeyboardButton(text="🌐 لینک وب معمولی", callback_data=f"ui_cbtn_type:{screen_key}:url", style="primary")],
        [InlineKeyboardButton(text="📲 اپ‌لینک (Web App داخل تلگرام)", callback_data=f"ui_cbtn_type:{screen_key}:webapp", style="primary")],
        [InlineKeyboardButton(text="🔙 انصراف", callback_data=f"ui_cbtn_list:{screen_key}", style="danger")],
    ])


def quick_destination_keyboard(screen_key: str):
    rows = [[InlineKeyboardButton(text=label, callback_data=f"ui_cbtn_dest:{screen_key}:{cb}", style="primary")] for cb, label in QUICK_DESTINATIONS]
    rows.append([InlineKeyboardButton(text="🔙 انصراف", callback_data=f"ui_cbtn_list:{screen_key}", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def screen_preview_markup(key: str):
    sc = SCREENS.get(key) or {}
    labels = dict(sc.get("buttons") or [])
    order = get_order(key)
    lay = get_layout(key)
    visible = []
    for cb in order:
        if cb not in labels or get_button_meta(key, cb)["hidden"]:
            continue
        visible.append(InlineKeyboardButton(text=get_button(key, cb, labels[cb]), callback_data=cb[:64], style="primary"))
    cols = lay["columns"] if lay["mode"] == "inline" else 1
    return InlineKeyboardMarkup(inline_keyboard=[visible[i:i+cols] for i in range(0, len(visible), cols)])


CATEGORY_LABELS = {
    # کاربران
    "start":          "🚀 شروع",
    "main_menu":      "🏠 منوی اصلی",
    "shop":           "🛒 خرید",
    "renewal":        "🔁 تمدید",
    "services":       "📱 سرویس‌ها",
    "free_test":      "🎁 تست",
    "custom_service": "🛠 سرویس سفارشی",
    "wallet":         "💰 کیف پول",
    "payment":        "💳 پرداخت",
    "invoice":        "🧧 فاکتور",
    "discount":       "🎟 تخفیف",
    "referral":       "🤝 رفرال",
    "agency":         "🤝 نمایندگی",
    "active_services":"🟢 سرویس‌های فعال",
    "expired_services":"🔴 سرویس‌های منقضی",
    "errors":         "🙅 پیام‌های خطا",
    "success":        "✅ پیام‌های موفقیت",
    "help":           "📚 راهنما",
    "support":        "🎫 پشتیبانی",
    # مدیریت
    "admin":          "🛡 پنل مدیریت",
    "admin_users":    "👥 مدیریت کاربران",
    "admin_stats":    "📊 آمار",
    "admin_tickets":  "🎫 تیکت‌ها",
    "admin_broadcast":"📢 پیام همگانی",
    "admin_orders":   "📋 سفارش‌ها",
    "admin_payments": "💰 پرداخت‌ها",
    "admin_receipts": "🧧 رسیدها",
    "admin_services": "📱 سرویس‌ها",
    "admin_panels":   "🖥 پنل‌ها",
    "admin_vip":      "🚀 VIP",
    "admin_test":     "🎁 تست",
    "admin_renewal":  "🔁 تمدید",
    "admin_agents":   "🤝 نمایندگان",
    "admin_discount": "🎟 تخفیف‌ها",
    "admin_referral": "🤝 رفرال",
    "admin_wallet":   "💰 کیف پول",
    "admin_payg":     "⚡ Pay As You Go",
    "admin_miniapp":  "🏠 Mini App",
    "admin_settings": "⚙️ تنظیمات",
    "finance":        "💰 مالی",
}
