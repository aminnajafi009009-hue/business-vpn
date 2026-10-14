"""
payment_manager_v3.py
مدیریت کامل روش‌های پرداخت با قابلیت فعال/غیرفعال هر روش
هر روش به صورت مجزا در DB ذخیره می‌شود.
ارزهای دیجیتال دینامیک — می‌توان هر ارزی اضافه کرد.
"""
from __future__ import annotations
import json
import logging
from dataclasses import dataclass, field, asdict
from typing import Optional

logger = logging.getLogger(__name__)

# ================================================================
# Data classes
# ================================================================

@dataclass
class CryptoConfig:
    symbol: str
    name: str
    network: str
    emoji: str
    custom_emoji_id: str | None = None
    enabled: bool = True
    wallet: str = ""
    margin_percent: float = 3.0   # extra margin on top of rate
    min_amount_toman: int = 50_000
    backup_price: float | None = None  # قیمت Fallback (بند ۲۳)


    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "CryptoConfig":
        if isinstance(d, cls):
            return cls(**d.to_dict())
        if not isinstance(d, dict):
            return cls(symbol="UNK", name="Unknown", network="Unknown", emoji="💎")
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ================================================================
# ارزهای پیش‌فرض (تعریف بعد از کلاس CryptoConfig)
# ================================================================
DEFAULT_CRYPTOS = [
    CryptoConfig(symbol="TRX", name="ترون", network="TRC20", emoji="🟣",
                 enabled=True, wallet="", margin_percent=3.0, min_amount_toman=50000, backup_price=None),
    CryptoConfig(symbol="TON", name="تون", network="TON", emoji="💎",
                 enabled=True, wallet="", margin_percent=3.0, min_amount_toman=50000, backup_price=None),
]


@dataclass
class CardConfig:
    enabled: bool = True
    card_number: str = ""
    owner_name: str = ""
    bank_name: str = ""
    auto_confirm: bool = False   # ادمین باید تایید کند
    receipt_required: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "CardConfig":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class WalletConfig:
    enabled: bool = True
    min_charge: int = 10_000
    max_charge: int = 50_000_000
    gift_percent: float = 0.0    # درصد هدیه هنگام شارج
    bonus_threshold: int = 0     # شارج بیشتر از X جایزه بگیرد

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "WalletConfig":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class UniquePayConfig:
    enabled: bool = False
    business_token: str = ""
    redirect_url: str = ""
    callback_url: str = ""
    min_amount: int = 50_000
    auto_confirm: bool = True    # پرداخت خودکار تایید می‌شود
    use_telegram_link: bool = True   # لینک تلگرام بده یا وب
    show_card_white_label: bool = False  # نمایش شماره کارت در ربات
    use_ddbot_compat: bool = False       # استفاده از endpoint سازگار DDBot

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "UniquePayConfig":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class PayAsYouGoConfig:
    enabled: bool = False
    price_per_gb: int = 5_000     # تومان به ازای هر GB
    price_per_day: int = 0        # تومان به ازای هر روز (اختیاری)
    min_balance: int = 20_000     # حداقل موجودی کیف پول برای شروع
    auto_suspend_below: int = 1_000  # قطع سرویس زیر این مقدار

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PayAsYouGoConfig":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ================================================================
# PaymentManager — مدیریت مرکزی
# ================================================================

class PaymentManager:
    """
    همه تنظیمات روش‌های پرداخت در یک کلاس — از DB خوانده و در DB ذخیره می‌شود.
    هنگام نیاز، ویا `await pm.load()` مقداردهی را از DB بارگزاری کنید.
    """
    def __init__(self, db_get_fn, db_set_fn):
        """
        db_get_fn(key: str) -> str   یا None
        db_set_fn(key: str, value: str)
        """
        self._get = db_get_fn
        self._set = db_set_fn
        self._card = CardConfig()
        self._wallet = WalletConfig()
        self._uniquepay = UniquePayConfig()
        self._paygo = PayAsYouGoConfig()
        self._cryptos: list[CryptoConfig] = []

    # ---- load / save ----

    def load(self):
        self._card = CardConfig.from_dict(
            json.loads(self._get("pm_card") or "{}"))
        self._wallet = WalletConfig.from_dict(
            json.loads(self._get("pm_wallet") or "{}"))
        self._uniquepay = UniquePayConfig.from_dict(
            json.loads(self._get("pm_uniquepay") or "{}"))
        self._paygo = PayAsYouGoConfig.from_dict(
            json.loads(self._get("pm_paygo") or "{}"))
        raw_cryptos = self._get("pm_cryptos")
        if raw_cryptos:
            try:
                raw_items = json.loads(raw_cryptos)
                self._cryptos = [CryptoConfig.from_dict(c) for c in raw_items]
            except Exception:
                self._cryptos = [CryptoConfig.from_dict(c) for c in DEFAULT_CRYPTOS]
        else:
            self._cryptos = [CryptoConfig.from_dict(c) for c in DEFAULT_CRYPTOS]

    def _save_cryptos(self):
        self._set("pm_cryptos", json.dumps([c.to_dict() for c in self._cryptos]))

    def _save_card(self):
        self._set("pm_card", json.dumps(self._card.to_dict()))

    def _save_wallet(self):
        self._set("pm_wallet", json.dumps(self._wallet.to_dict()))

    def _save_uniquepay(self):
        self._set("pm_uniquepay", json.dumps(self._uniquepay.to_dict()))

    def _save_paygo(self):
        self._set("pm_paygo", json.dumps(self._paygo.to_dict()))

    # ---- Card ----

    @property
    def card(self) -> CardConfig:
        return self._card

    def update_card(self, **kwargs):
        # backward compat: "number" → "card_number", "owner" → "owner_name"
        aliases = {"number": "card_number", "owner": "owner_name"}
        for k, v in list(kwargs.items()):
            real_key = aliases.get(k, k)
            if hasattr(self._card, real_key):
                setattr(self._card, real_key, v)
        self._save_card()

    # ---- Wallet ----

    @property
    def wallet(self) -> WalletConfig:
        return self._wallet

    def update_wallet(self, **kwargs):
        for k, v in kwargs.items():
            if hasattr(self._wallet, k):
                setattr(self._wallet, k, v)
        self._save_wallet()

    # ---- UniquePay ----

    @property
    def uniquepay(self) -> UniquePayConfig:
        return self._uniquepay

    def update_uniquepay(self, **kwargs):
        for k, v in kwargs.items():
            if hasattr(self._uniquepay, k):
                setattr(self._uniquepay, k, v)
        self._save_uniquepay()

    def get_uniquepay_client(self):
        from uniquepay import get_client
        if not self._uniquepay.enabled or not self._uniquepay.business_token:
            return None
        return get_client(self._uniquepay.business_token)

    # ---- Pay-as-you-go ----

    @property
    def paygo(self) -> PayAsYouGoConfig:
        return self._paygo

    def update_paygo(self, **kwargs):
        for k, v in kwargs.items():
            if hasattr(self._paygo, k):
                setattr(self._paygo, k, v)
        self._save_paygo()

    # ---- Crypto ----

    @property
    def cryptos(self) -> list[CryptoConfig]:
        return self._cryptos

    @property
    def crypto_master_enabled(self) -> bool:
        """کلید کلی بخش رمزارز؛ خاموش‌کردنش به تنظیمات تک‌تک ارزها دست نمی‌زند."""
        try:
            return str(self._get("pm_crypto_master") or "1") != "0"
        except Exception:
            return True

    def set_crypto_master_enabled(self, value: bool) -> None:
        self._set("pm_crypto_master", "1" if value else "0")

    @property
    def enabled_cryptos(self) -> list[CryptoConfig]:
        if not self.crypto_master_enabled:
            return []
        return [c for c in self._cryptos if c.enabled]

    def get_crypto(self, symbol: str) -> Optional[CryptoConfig]:
        return next((c for c in self._cryptos if c.symbol.upper() == symbol.upper()), None)

    def add_crypto(self, symbol: str, name: str, network: str, emoji: str,
                   wallet: str = "", margin_percent: float = 3.0,
                   custom_emoji_id: str | None = None) -> CryptoConfig:
        existing = self.get_crypto(symbol)
        if existing:
            existing.enabled = True
            existing.wallet = wallet or existing.wallet
            self._save_cryptos()
            return existing
        c = CryptoConfig(symbol=symbol.upper(), name=name, network=network,
                         emoji=emoji, custom_emoji_id=custom_emoji_id,
                         wallet=wallet, margin_percent=margin_percent)
        self._cryptos.append(c)
        self._save_cryptos()
        return c

    def remove_crypto(self, symbol: str) -> bool:
        before = len(self._cryptos)
        self._cryptos = [c for c in self._cryptos if c.symbol.upper() != symbol.upper()]
        if len(self._cryptos) < before:
            self._save_cryptos()
            return True
        return False

    def toggle_crypto(self, symbol: str) -> Optional[bool]:
        c = self.get_crypto(symbol)
        if not c:
            return None
        c.enabled = not c.enabled
        self._save_cryptos()
        return c.enabled

    def update_crypto(self, symbol: str, **kwargs):
        c = self.get_crypto(symbol)
        if not c:
            return
        for k, v in kwargs.items():
            if hasattr(c, k):
                setattr(c, k, v)
        self._save_cryptos()

    # ---- General ----

    def active_methods(self) -> list[str]:
        methods = []
        if self._card.enabled and self._card.card_number:
            methods.append("card")
        if self._wallet.enabled:
            methods.append("wallet")
        if self._uniquepay.enabled and self._uniquepay.business_token:
            methods.append("uniquepay")
        if self.enabled_cryptos:
            methods.append("crypto")
        if self._paygo.enabled:
            methods.append("paygo")
        return methods

    def summary(self) -> str:
        """Summary for admin panel."""
        lines = []
        e = lambda b: "✅" if b else "❌"
        lines.append(f"💳 کارت‌به‌کارت: {e(self._card.enabled)} {'| ' + self._card.card_number[-4:] if self._card.card_number else ''}")
        lines.append(f"💰 کیف پول: {e(self._wallet.enabled)}")
        lines.append(f"🌐 پرداخت آنلاین (UniquePay): {e(self._uniquepay.enabled)}")
        lines.append(f"🔄 پرداخت به ازای مصرف: {e(self._paygo.enabled)}")
        enabled_c = [c.symbol for c in self._cryptos if c.enabled]
        lines.append(f"📊 ارز دیجیتال: {', '.join(enabled_c) or 'غیرفعال'}")
        return "\n".join(lines)


async def get_crypto_rate_with_fallback(
    symbol: str,
    fetch_fn,
    database_module=None,
    timeout: float = 10.0,
) -> float | None:
    """گرفتن قیمت ارز با Fallback دستی (بند ۲۳).

    Args:
        symbol: سیمبل ارز (مثلاً USDT)
        fetch_fn: coroutine یا callable که قیمت را از API می‌گیرد
        database_module: ماجول database برای دسترسی به get_crypto_backup_price
        timeout: حداکثر ثانیه برای درخواست API

    Returns:
        قیمت (float) یا None اگر هیچ منبعی نداشته باشد
    """
    import asyncio
    import logging
    _logger = logging.getLogger(__name__)

    # Try live API first
    try:
        import asyncio as _aio
        if asyncio.iscoroutinefunction(fetch_fn):
            rate = await asyncio.wait_for(fetch_fn(), timeout=timeout)
        else:
            rate = fetch_fn()
        if rate and float(rate) > 0:
            return float(rate)
    except asyncio.TimeoutError:
        _logger.warning(f'Crypto rate API timeout for {symbol}, using backup price')
    except Exception as e:
        _logger.warning(f'Crypto rate API error for {symbol}: {e}, using backup price')

    # Fallback to manual backup price from DB
    if database_module is not None:
        try:
            enabled = getattr(database_module, 'get_crypto_backup_enabled', lambda s: True)(symbol)
            if enabled:
                backup = getattr(database_module, 'get_crypto_backup_price', lambda s: None)(symbol)
                if backup and float(backup) > 0:
                    _logger.info(f'Using backup price for {symbol}: {backup}')
                    return float(backup)
        except Exception as e:
            _logger.error(f'Error reading backup price for {symbol}: {e}')

    return None
