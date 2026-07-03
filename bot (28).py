"""
GenRDP Renewal Bot — v5
========================
Architettura:
  - clients: un Telegram user
  - proxies: un proxy_access iProxy (conn_id + proxy_id + porta), assegnato a un cliente
  - Clienti normali: rinnovo via prolongate-plan (connessione) + update expires_at (proxy access)
  - Reseller: scadenza manuale nel DB, no chiamata iProxy al rinnovo
  - Pagamenti: Carta/Alipay/Google Pay (Stripe) | PayPal (+3%) | Crypto (CoinGate)
  - Lingue: EN, IT, ZH, RU — persistita per utente
  - Admin panel: completamente guidato via bottoni inline
  - Job orario: reminder reseller 48h e 24h, alert scaduto
"""

import os, logging, sqlite3, httpx, stripe, json, threading, asyncio, math
from datetime import datetime, timezone, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters,
)
from flask import Flask, request as flask_request, jsonify
from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(name)s | %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN        = os.environ["TELEGRAM_TOKEN"]
IPROXY_API_KEY        = os.environ["IPROXY_API_KEY"]
STRIPE_SECRET         = os.environ["STRIPE_SECRET_KEY"]
STRIPE_WEBHOOK_SECRET = os.environ["STRIPE_WEBHOOK_SECRET"]
PAYPAL_CLIENT_ID      = os.environ["PAYPAL_CLIENT_ID"]
PAYPAL_CLIENT_SECRET  = os.environ["PAYPAL_CLIENT_SECRET"]
PAYPAL_MODE           = os.environ.get("PAYPAL_MODE", "live")
COINGATE_API_KEY      = os.environ["COINGATE_API_KEY"]
COINGATE_MODE         = os.environ.get("COINGATE_MODE", "live")   # "sandbox" | "live"
BASE_URL              = os.environ["BASE_URL"]
ADMIN_TELEGRAM_IDS    = set(int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x)
NOTIFY_USERNAME       = "@username"

stripe.api_key   = STRIPE_SECRET
IPROXY_BASE      = "https://iproxy.online/api/console/v1"
PAYPAL_BASE      = "https://api-m.paypal.com" if PAYPAL_MODE == "live" else "https://api-m.sandbox.paypal.com"
COINGATE_BASE    = "https://api.coingate.com" if COINGATE_MODE == "live" else "https://api-sandbox.coingate.com"

DEFAULT_PRICES   = {"limited": 45.00, "unlimited": 60.00}
PAYPAL_FEE_PCT   = 0.03
RENEWAL_DAYS     = 30
SUPPORTED_LANGS  = ["en", "it", "zh", "ru"]

# ── i18n ───────────────────────────────────────────────────────────────────────
T = {
    "choose_lang": {
        "en": "🌐 Please choose your language:",
        "it": "🌐 Scegli la tua lingua:",
        "zh": "🌐 请选择您的语言：",
        "ru": "🌐 Пожалуйста, выберите язык:",
    },
    "lang_set": {
        "en": "✅ Language set to English.",
        "it": "✅ Lingua impostata su Italiano.",
        "zh": "✅ 语言已设置为中文。",
        "ru": "✅ Язык установлен на Русский.",
    },
    "not_registered": {
        "en": "👋 Welcome to *GenRDP*!\n\nYour account is not registered yet. Please contact support to activate your subscription.",
        "it": "👋 Benvenuto su *GenRDP*!\n\nNon sei ancora registrato. Contatta il supporto per attivare il tuo account.",
        "zh": "👋 欢迎使用 *GenRDP*！\n\n您尚未注册，请联系客服以激活账户。",
        "ru": "👋 Добро пожаловать в *GenRDP*!\n\nВаш аккаунт не зарегистрирован. Свяжитесь с поддержкой.",
    },
    "loading": {
        "en": "⏳ Loading...",
        "it": "⏳ Caricamento...",
        "zh": "⏳ 加载中...",
        "ru": "⏳ Загрузка...",
    },
    "error_iproxy": {
        "en": "❌ Could not retrieve proxy info. Please try again later.",
        "it": "❌ Errore nel recupero del proxy. Riprova tra poco.",
        "zh": "❌ 无法获取代理信息，请稍后重试。",
        "ru": "❌ Не удалось получить информацию о прокси. Попробуйте позже.",
    },
    "status_msg": {
        "en": "📡 *{name}*\nPlan: *{plan}* — €{price:.2f}/month\n📅 Expiry: *{expiry}*\n\nRenew for *30 days*. Choose payment method:",
        "it": "📡 *{name}*\nPiano: *{plan}* — €{price:.2f}/mese\n📅 Scadenza: *{expiry}*\n\nRinnova per *30 giorni*. Scegli il metodo di pagamento:",
        "zh": "📡 *{name}*\n套餐：*{plan}* — €{price:.2f}/月\n📅 到期：*{expiry}*\n\n续费 *30 天*，请选择支付方式：",
        "ru": "📡 *{name}*\nТариф: *{plan}* — €{price:.2f}/мес\n📅 Истекает: *{expiry}*\n\nПродлить на *30 дней*. Выберите способ оплаты:",
    },
    "proxy_select": {
        "en": "Select a proxy to manage:",
        "it": "Seleziona un proxy da gestire:",
        "zh": "选择要管理的代理：",
        "ru": "Выберите прокси для управления:",
    },
    "main_menu": {
        "en": "👋 Welcome to *GenRDP*!\nWhat would you like to do?",
        "it": "👋 Benvenuto su *GenRDP*!\nCosa vuoi fare?",
        "zh": "👋 欢迎使用 *GenRDP*！\n您想做什么？",
        "ru": "👋 Добро пожаловать в *GenRDP*!\nЧто вы хотите сделать?",
    },
    "btn_manage": {
        "en": "📋 Manage Proxies",
        "it": "📋 Gestisci Proxy",
        "zh": "📋 管理代理",
        "ru": "📋 Управление прокси",
    },
    "btn_renew_proxy": {
        "en": "💳 Renew this proxy",
        "it": "💳 Rinnova questo proxy",
        "zh": "💳 续费此代理",
        "ru": "💳 Продлить этот прокси",
    },
    "proxy_action_menu": {
        "en": "📡 *{name}*\n📅 Expiry: *{expiry}*\nWhat would you like to do?",
        "it": "📡 *{name}*\n📅 Scadenza: *{expiry}*\nCosa vuoi fare?",
        "zh": "📡 *{name}*\n📅 到期：*{expiry}*\n您想做什么？",
        "ru": "📡 *{name}*\n📅 Истекает: *{expiry}*\nЧто вы хотите сделать?",
    },
    "proxy_menu": {
        "en": "📡 *{name}*\nPlan: *{plan}* — €{price:.2f}/month\n📅 Expiry: *{expiry}*\nWhat would you like to do?",
        "it": "📡 *{name}*\nPiano: *{plan}* — €{price:.2f}/mese\n📅 Scadenza: *{expiry}*\nCosa vuoi fare?",
        "zh": "📡 *{name}*\n套餐：*{plan}* — €{price:.2f}/月\n📅 到期：*{expiry}*\n您想做什么？",
        "ru": "📡 *{name}*\nТариф: *{plan}* — €{price:.2f}/мес\n📅 Истекает: *{expiry}*\nЧто вы хотите сделать?",
    },
    "btn_renew": {
        "en": "💳 Renew — €{price:.2f}",
        "it": "💳 Rinnova — €{price:.2f}",
        "zh": "💳 续费 — €{price:.2f}",
        "ru": "💳 Продлить — €{price:.2f}",
    },
    "btn_card": {
        "en": "💳 Card / Alipay / Google Pay — €{price:.2f}",
        "it": "💳 Carta / Alipay / Google Pay — €{price:.2f}",
        "zh": "💳 银行卡 / 支付宝 / Google Pay — €{price:.2f}",
        "ru": "💳 Карта / Alipay / Google Pay — €{price:.2f}",
    },
    "btn_paypal": {
        "en": "🅿️ PayPal (+3% fee) — €{price:.2f}",
        "it": "🅿️ PayPal (+3% commissione) — €{price:.2f}",
        "zh": "🅿️ PayPal（+3% 手续费）— €{price:.2f}",
        "ru": "🅿️ PayPal (+3% комиссия) — €{price:.2f}",
    },
    "btn_crypto": {
        "en": "₿ Crypto (USDT, BTC, ETH…) — €{price:.2f}",
        "it": "₿ Crypto (USDT, BTC, ETH…) — €{price:.2f}",
        "zh": "₿ 加密货币 (USDT, BTC, ETH…) — €{price:.2f}",
        "ru": "₿ Крипто (USDT, BTC, ETH…) — €{price:.2f}",
    },
    "generating": {
        "en": "⏳ Generating payment link...",
        "it": "⏳ Generazione link di pagamento...",
        "zh": "⏳ 正在生成付款链接...",
        "ru": "⏳ Создание ссылки на оплату...",
    },
    "pay_error": {
        "en": "❌ Error generating payment link. Please try again.",
        "it": "❌ Errore nella creazione del pagamento. Riprova.",
        "zh": "❌ 生成付款链接失败，请重试。",
        "ru": "❌ Ошибка при создании ссылки. Попробуйте снова.",
    },
    "pay_ready_card": {
        "en": "✅ Payment link ready!\n\n📦 30-day renewal — €{price:.2f}\nAccepted: Card, Alipay, Google Pay\n\nYou have 10 minutes to complete the payment.",
        "it": "✅ Link di pagamento pronto!\n\n📦 Rinnovo 30 giorni — €{price:.2f}\nAccettati: Carta, Alipay, Google Pay\n\nHai 10 minuti per completare il pagamento.",
        "zh": "✅ 付款链接已生成！\n\n📦 续费 30 天 — €{price:.2f}\n支持：银行卡、支付宝、Google Pay\n\n请在 10 分钟内完成支付。",
        "ru": "✅ Ссылка готова!\n\n📦 Продление 30 дней — €{price:.2f}\nПринимаются: карта, Alipay, Google Pay\n\nУ вас 10 минут для оплаты.",
    },
    "pay_ready_paypal": {
        "en": "✅ PayPal link ready!\n\n📦 30-day renewal\nBase: €{base:.2f}\nPayPal fee (+3%): €{fee:.2f}\n━━━━━━━━━━\n*Total: €{total:.2f}*\n\nYou have 10 minutes to complete.",
        "it": "✅ Link PayPal pronto!\n\n📦 Rinnovo 30 giorni\nBase: €{base:.2f}\nCommissione PayPal (+3%): €{fee:.2f}\n━━━━━━━━━━\n*Totale: €{total:.2f}*\n\nHai 10 minuti per completare.",
        "zh": "✅ PayPal 链接已生成！\n\n📦 续费 30 天\n基础：€{base:.2f}\nPayPal 手续费（+3%）：€{fee:.2f}\n━━━━━━━━━━\n*合计：€{total:.2f}*\n\n请在 10 分钟内完成。",
        "ru": "✅ Ссылка PayPal готова!\n\n📦 Продление 30 дней\nБаза: €{base:.2f}\nКомиссия PayPal (+3%): €{fee:.2f}\n━━━━━━━━━━\n*Итого: €{total:.2f}*\n\nУ вас 10 минут.",
    },
    "pay_ready_crypto": {
        "en": "✅ Crypto payment link ready!\n\n📦 30-day renewal — €{price:.2f}\nAccepted: USDT, BTC, ETH, LTC and more\n\nThe link expires in 60 minutes.",
        "it": "✅ Link crypto pronto!\n\n📦 Rinnovo 30 giorni — €{price:.2f}\nAccettati: USDT, BTC, ETH, LTC e altri\n\nIl link scade in 60 minuti.",
        "zh": "✅ 加密货币付款链接已生成！\n\n📦 续费 30 天 — €{price:.2f}\n支持：USDT、BTC、ETH、LTC 等\n\n链接 60 分钟内有效。",
        "ru": "✅ Ссылка для крипто-оплаты готова!\n\n📦 Продление 30 дней — €{price:.2f}\nПринимаются: USDT, BTC, ETH, LTC и другие\n\nСсылка действительна 60 минут.",
    },
    "btn_pay_card": {
        "en": "💳 Pay Now",
        "it": "💳 Paga ora",
        "zh": "💳 立即支付",
        "ru": "💳 Оплатить",
    },
    "btn_pay_paypal": {
        "en": "🅿️ Pay with PayPal",
        "it": "🅿️ Paga con PayPal",
        "zh": "🅿️ 用 PayPal 支付",
        "ru": "🅿️ Оплатить PayPal",
    },
    "btn_pay_crypto": {
        "en": "₿ Pay with Crypto",
        "it": "₿ Paga con Crypto",
        "zh": "₿ 用加密货币支付",
        "ru": "₿ Оплатить криптой",
    },
    "session_expired": {
        "en": "❌ Session expired. Use /start to begin again.",
        "it": "❌ Sessione scaduta. Usa /start per ricominciare.",
        "zh": "❌ 会话已过期，请使用 /start 重新开始。",
        "ru": "❌ Сессия истекла. Используйте /start.",
    },
    "renewal_ok": {
        "en": "✅ *Renewal successful!*\n\n📡 Proxy: *{name}*\n🔌 Port: `{port}`\n📅 New expiry: *{expiry}*\n\nThank you for choosing GenRDP! 🚀",
        "it": "✅ *Rinnovo completato!*\n\n📡 Proxy: *{name}*\n🔌 Porta: `{port}`\n📅 Nuova scadenza: *{expiry}*\n\nGrazie per aver scelto GenRDP! 🚀",
        "zh": "✅ *续费成功！*\n\n📡 代理：*{name}*\n🔌 端口：`{port}`\n📅 新到期时间：*{expiry}*\n\n感谢选择 GenRDP！🚀",
        "ru": "✅ *Продление успешно!*\n\n📡 Прокси: *{name}*\n🔌 Порт: `{port}`\n📅 Новый срок: *{expiry}*\n\nСпасибо за GenRDP! 🚀",
    },
    "renewal_ok_reseller": {
        "en": "✅ *Renewal successful!*\n\n📡 Proxy: *{name}*\n📅 New expiry: *{expiry}*\n\nThank you! 🚀",
        "it": "✅ *Rinnovo completato!*\n\n📡 Proxy: *{name}*\n📅 Nuova scadenza: *{expiry}*\n\nGrazie! 🚀",
        "zh": "✅ *续费成功！*\n\n📡 代理：*{name}*\n📅 新到期时间：*{expiry}*\n\n感谢！🚀",
        "ru": "✅ *Продление успешно!*\n\n📡 Прокси: *{name}*\n📅 Новый срок: *{expiry}*\n\nСпасибо! 🚀",
    },
    "change_password": {
        "en": "🔑 *Change proxy password*\n\n📡 *{name}*\n\nEnter a new password (min 6 characters):",
        "it": "🔑 *Cambia password proxy*\n\n📡 *{name}*\n\nInserisci una nuova password (min 6 caratteri):",
        "zh": "🔑 *修改代理密码*\n\n📡 *{name}*\n\n请输入新密码（至少6位）：",
        "ru": "🔑 *Смена пароля прокси*\n\n📡 *{name}*\n\nВведите новый пароль (мин. 6 символов):",
    },
    "password_ok": {
        "en": "✅ Password updated successfully for *{name}*!",
        "it": "✅ Password aggiornata con successo per *{name}*!",
        "zh": "✅ *{name}* 的密码已成功更新！",
        "ru": "✅ Пароль успешно обновлён для *{name}*!",
    },
    "password_fail": {
        "en": "❌ Failed to update password. Please try again later.",
        "it": "❌ Errore nell'aggiornamento della password. Riprova tra poco.",
        "zh": "❌ 密码更新失败，请稍后重试。",
        "ru": "❌ Не удалось обновить пароль. Попробуйте позже.",
    },
    "password_short": {
        "en": "❌ Password too short. Minimum 6 characters.",
        "it": "❌ Password troppo corta. Minimo 6 caratteri.",
        "zh": "❌ 密码太短，至少需要6个字符。",
        "ru": "❌ Пароль слишком короткий. Минимум 6 символов.",
    },
    "btn_change_password": {
        "en": "🔑 Change Password",
        "it": "🔑 Cambia Password",
        "zh": "🔑 修改密码",
        "ru": "🔑 Сменить пароль",
    },
    "renewal_fail": {
        "en": "⚠️ Payment received, but an error occurred during automatic renewal.\n\nThe GenRDP team has been notified and will handle it manually shortly.",
        "it": "⚠️ Pagamento ricevuto, ma si è verificato un errore nel rinnovo automatico.\n\nIl team GenRDP è stato notificato e provvederà manualmente.",
        "zh": "⚠️ 付款成功，但自动续费时出错。\n\nGenRDP 团队已收到通知，将尽快手动处理。",
        "ru": "⚠️ Платёж прошёл, но при продлении произошла ошибка.\n\nКоманда GenRDP уведомлена и скоро всё исправит.",
    },
    "renew_all_btn": {
        "en": "🔄 Renew all expiring ({count}) — €{total:.2f}",
        "it": "🔄 Rinnova tutti in scadenza ({count}) — €{total:.2f}",
        "zh": "🔄 续费所有即将到期 ({count}) — €{total:.2f}",
        "ru": "🔄 Продлить все истекающие ({count}) — €{total:.2f}",
    },
    "renew_all_ready_card": {
        "en": "✅ Payment link ready!\n📦 Renewing *{count} proxies* — €{total:.2f}\nAccepted: Card, Alipay, Google Pay",
        "it": "✅ Link pronto!\n📦 Rinnovo *{count} proxy* — €{total:.2f}\nAccettati: Carta, Alipay, Google Pay",
        "zh": "✅ 付款链接已生成！\n📦 续费 *{count} 个代理* — €{total:.2f}\n支持：银行卡、支付宝、Google Pay",
        "ru": "✅ Ссылка готова!\n📦 Продление *{count} прокси* — €{total:.2f}\nПринимаются: карта, Alipay, Google Pay",
    },
    "history_title": {
        "en": "📋 *Your proxy history*",
        "it": "📋 *Il tuo storico proxy*",
        "zh": "📋 *您的代理记录*",
        "ru": "📋 *История вашего прокси*",
    },
    "history_active": {
        "en": "📡 *Active proxies & expiry:*",
        "it": "📡 *Proxy attivi & scadenze:*",
        "zh": "📡 *活跃代理与到期时间：*",
        "ru": "📡 *Активные прокси и сроки:*",
    },
    "history_payments": {
        "en": "💳 *Recent payments:*",
        "it": "💳 *Pagamenti recenti:*",
        "zh": "💳 *近期付款记录：*",
        "ru": "💳 *Последние платежи:*",
    },
    "history_no_payments": {
        "en": "_No payments recorded yet._",
        "it": "_Nessun pagamento registrato._",
        "zh": "_暂无付款记录。_",
        "ru": "_Платежей пока нет._",
    },
    "client_reminder_48h": {
        "en": (
            "⚠️ *Proxy expiring soon*\n\n"
            "📡 *{name}*\n"
            "📅 Expiry: *{expiry}*\n\n"
            "Your proxy expires in *48 hours*. Renew now to avoid interruption.\n\n"
            "Use /start to renew."
        ),
        "it": (
            "⚠️ *Proxy in scadenza*\n\n"
            "📡 *{name}*\n"
            "📅 Scadenza: *{expiry}*\n\n"
            "Il tuo proxy scade tra *48 ore*. Rinnova ora per evitare l'interruzione.\n\n"
            "Usa /start per rinnovare."
        ),
        "zh": (
            "⚠️ *代理即将到期*\n\n"
            "📡 *{name}*\n"
            "📅 到期：*{expiry}*\n\n"
            "您的代理将在 *48小时* 后到期，请立即续费。\n\n"
            "使用 /start 续费。"
        ),
        "ru": (
            "⚠️ *Прокси истекает скоро*\n\n"
            "📡 *{name}*\n"
            "📅 Истекает: *{expiry}*\n\n"
            "Ваш прокси истекает через *48 часов*. Продлите сейчас.\n\n"
            "Используйте /start для продления."
        ),
    },
    "reseller_reminder_48h": {
        "en": "⚠️ *Proxy expiring in 48 hours*\n\n📡 *{name}*\n📅 Expiry: *{expiry}*\n\nRenew NOW to avoid interruption.\n\n⛔ *Late payments will result in immediate termination — no exceptions.*\n\nUse /start to renew.",
        "it": "⚠️ *Proxy in scadenza tra 48 ore*\n\n📡 *{name}*\n📅 Scadenza: *{expiry}*\n\nRinnova ORA per evitare l'interruzione.\n\n⛔ *In caso di ritardo il proxy verrà disattivato senza eccezioni.*\n\nUsa /start per rinnovare.",
        "zh": "⚠️ *代理将在 48 小时后到期*\n\n📡 *{name}*\n📅 到期：*{expiry}*\n\n请立即续费以避免中断。\n\n⛔ *逾期付款将立即终止服务，不接受任何例外。*\n\n使用 /start 续费。",
        "ru": "⚠️ *Прокси истекает через 48 часов*\n\n📡 *{name}*\n📅 Истекает: *{expiry}*\n\nПродлите СЕЙЧАС.\n\n⛔ *Задержка оплаты = немедленное отключение, без исключений.*\n\nИспользуйте /start.",
    },
    "reseller_reminder_24h": {
        "en": "🚨 *FINAL WARNING — Proxy expires in 24 hours*\n\n📡 *{name}*\n📅 Expiry: *{expiry}*\n\nRenew IMMEDIATELY.\n\n⛔ *No exceptions. No delays accepted.*\n\nUse /start now.",
        "it": "🚨 *AVVISO FINALE — Proxy scade tra 24 ore*\n\n📡 *{name}*\n📅 Scadenza: *{expiry}*\n\nRinnova IMMEDIATAMENTE.\n\n⛔ *Nessuna eccezione. Nessun ritardo accettato.*\n\nUsa /start ora.",
        "zh": "🚨 *最终警告 — 代理将在 24 小时后到期*\n\n📡 *{name}*\n📅 到期：*{expiry}*\n\n请立即续费。\n\n⛔ *无任何例外，不接受延迟。*\n\n立即使用 /start。",
        "ru": "🚨 *ПОСЛЕДНЕЕ ПРЕДУПРЕЖДЕНИЕ — Прокси истекает через 24 часа*\n\n📡 *{name}*\n📅 Истекает: *{expiry}*\n\nПродлите НЕМЕДЛЕННО.\n\n⛔ *Никаких исключений.*\n\nИспользуйте /start.",
    },
}

def t(key: str, lang: str, **kw) -> str:
    s = T[key].get(lang, T[key]["en"])
    return s.format(**kw) if kw else s

# ── DB ─────────────────────────────────────────────────────────────────────────
DB_PATH = os.environ.get("DB_PATH", "data/genrdp.db")

def db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c

def db_init():
    with db() as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS clients (
                telegram_id  INTEGER PRIMARY KEY,
                client_type  TEXT NOT NULL DEFAULT 'client',
                tg_username  TEXT,
                lang         TEXT NOT NULL DEFAULT 'en',
                added_at     TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS proxies (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id      INTEGER NOT NULL REFERENCES clients(telegram_id),
                conn_id          TEXT NOT NULL,
                proxy_id         TEXT,           -- iProxy proxy_access id (null for resellers)
                proxy_name       TEXT,           -- cached display name
                port             INTEGER,        -- cached port
                hostname         TEXT,           -- cached hostname
                plan_type        TEXT NOT NULL DEFAULT 'limited',
                price_override   REAL,
                reseller_expiry  TEXT,           -- YYYY-MM-DD for resellers only
                proxy_login      TEXT,           -- cached login from iProxy auth
                reminder_sent_48 INTEGER DEFAULT 0,
                reminder_sent_24 INTEGER DEFAULT 0,
                added_at         TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS pending_payments (
                payment_id   TEXT PRIMARY KEY,
                provider     TEXT NOT NULL,
                telegram_id  INTEGER NOT NULL,
                proxy_db_id  INTEGER NOT NULL,
                amount       REAL NOT NULL,
                lang         TEXT DEFAULT 'en',
                is_reseller  INTEGER DEFAULT 0,
                created_at   TEXT DEFAULT (datetime('now'))
            );
        """)
        # Offline tracker table
        c.execute("""CREATE TABLE IF NOT EXISTS offline_tracker (
            conn_id         TEXT PRIMARY KEY,
            first_offline   TEXT NOT NULL,
            last_action_at  TEXT,
            action_count    INTEGER DEFAULT 0
        )""")
        # Fix any proxies with plan_type='override' left from previous bug
        try:
            c.execute("UPDATE proxies SET plan_type='limited' WHERE plan_type='override' AND price_override IS NULL")
            c.execute("UPDATE proxies SET plan_type='limited' WHERE plan_type NOT IN ('limited','unlimited')")
        except Exception:
            pass
        # Migrations — add missing columns
        for tbl, col, defn in [
            ("clients", "tg_username", "TEXT"),
            ("proxies", "hostname", "TEXT"),
            ("proxies", "proxy_name", "TEXT"),
            ("proxies", "proxy_login", "TEXT"),
        ]:
            try:
                c.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} {defn}")
            except Exception:
                pass
        # Create payments table if missing (migration)
        try:
            c.execute("""CREATE TABLE IF NOT EXISTS payments (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id  INTEGER NOT NULL,
                proxy_db_id  INTEGER NOT NULL,
                provider     TEXT NOT NULL,
                amount       REAL NOT NULL,
                proxy_name   TEXT,
                paid_at      TEXT DEFAULT (datetime('now'))
            )""")
        except Exception:
            pass
        # Migrate pending_payments: drop old schema if it has conn_id instead of proxy_db_id
        pp_cols = [r[1] for r in c.execute("PRAGMA table_info(pending_payments)").fetchall()]
        if "conn_id" in pp_cols and "proxy_db_id" not in pp_cols:
            c.executescript("""
                DROP TABLE IF EXISTS pending_payments;
                CREATE TABLE pending_payments (
                    payment_id   TEXT PRIMARY KEY,
                    provider     TEXT NOT NULL,
                    telegram_id  INTEGER NOT NULL,
                    proxy_db_id  INTEGER NOT NULL,
                    amount       REAL NOT NULL,
                    lang         TEXT DEFAULT 'en',
                    is_reseller  INTEGER DEFAULT 0,
                    created_at   TEXT DEFAULT (datetime('now'))
                );
            """)

def get_client(tid: int):
    with db() as c:
        return c.execute("SELECT * FROM clients WHERE telegram_id=?", (tid,)).fetchone()

def get_all_clients():
    with db() as c:
        return c.execute("SELECT * FROM clients ORDER BY added_at DESC").fetchall()

def upsert_client(tid: int, client_type: str = "client", username: str | None = None):
    with db() as c:
        c.execute("""
            INSERT INTO clients (telegram_id, client_type, tg_username)
            VALUES (?,?,?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                client_type=excluded.client_type,
                tg_username=COALESCE(excluded.tg_username, tg_username)
        """, (tid, client_type, username))

def set_lang(tid: int, lang: str):
    with db() as c:
        c.execute("UPDATE clients SET lang=? WHERE telegram_id=?", (lang, tid))

def get_lang(client) -> str:
    if client and client["lang"] in SUPPORTED_LANGS:
        return client["lang"]
    return "en"

def get_proxies(tid: int):
    with db() as c:
        return c.execute("SELECT * FROM proxies WHERE telegram_id=? ORDER BY added_at", (tid,)).fetchall()

def get_proxy_by_id(proxy_db_id: int):
    with db() as c:
        return c.execute("SELECT * FROM proxies WHERE id=?", (proxy_db_id,)).fetchone()

def get_proxy_client(proxy_db_id: int):
    with db() as c:
        return c.execute(
            "SELECT cl.* FROM clients cl JOIN proxies p ON p.telegram_id=cl.telegram_id WHERE p.id=?",
            (proxy_db_id,)
        ).fetchone()

def add_proxy(tid: int, conn_id: str, proxy_id: str | None, proxy_name: str,
              port: int | None, hostname: str | None,
              plan_type: str, price_override: float | None = None,
              reseller_expiry: str | None = None,
              proxy_login: str | None = None) -> int:
    with db() as c:
        cur = c.execute("""
            INSERT INTO proxies (telegram_id, conn_id, proxy_id, proxy_name, port, hostname,
                                 plan_type, price_override, reseller_expiry, proxy_login)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (tid, conn_id, proxy_id, proxy_name, port, hostname,
              plan_type, price_override, reseller_expiry, proxy_login))
        return cur.lastrowid

def remove_proxy(proxy_db_id: int):
    with db() as c:
        c.execute("DELETE FROM proxies WHERE id=?", (proxy_db_id,))

def remove_client(tid: int):
    """Remove client and all their proxies."""""
    with db() as c:
        c.execute("DELETE FROM proxies WHERE telegram_id=?", (tid,))
        c.execute("DELETE FROM clients WHERE telegram_id=?", (tid,))

def move_proxy(proxy_db_id: int, new_tid: int):
    with db() as c:
        c.execute("UPDATE proxies SET telegram_id=? WHERE id=?", (new_tid, proxy_db_id))

def set_price_override(proxy_db_id: int, price: float):
    with db() as c:
        c.execute("UPDATE proxies SET price_override=? WHERE id=?", (price, proxy_db_id))

def set_reseller_expiry(proxy_db_id: int, expiry: str):
    with db() as c:
        c.execute("UPDATE proxies SET reseller_expiry=?, reminder_sent_48=0, reminder_sent_24=0 WHERE id=?",
                  (expiry, proxy_db_id))

def mark_reminder(proxy_db_id: int, hours: int):
    col = "reminder_sent_48" if hours == 48 else "reminder_sent_24"
    with db() as c:
        c.execute(f"UPDATE proxies SET {col}=1 WHERE id=?", (proxy_db_id,))

def extend_reseller_expiry(proxy_db_id: int) -> str:
    with db() as c:
        row = c.execute("SELECT reseller_expiry FROM proxies WHERE id=?", (proxy_db_id,)).fetchone()
    now = datetime.now(timezone.utc)
    base = now
    if row and row["reseller_expiry"]:
        try:
            base = datetime.fromisoformat(row["reseller_expiry"]).replace(tzinfo=timezone.utc)
            if base < now:
                base = now
        except Exception:
            pass
    new_exp = (base + timedelta(days=30)).strftime("%Y-%m-%d")
    set_reseller_expiry(proxy_db_id, new_exp)
    return new_exp

def effective_price(proxy_row) -> float:
    if proxy_row["price_override"] is not None:
        return float(proxy_row["price_override"])
    plan = proxy_row["plan_type"]
    return DEFAULT_PRICES.get(plan, DEFAULT_PRICES["limited"])

def pp_price(base: float) -> float:
    return math.ceil(base * (1 + PAYPAL_FEE_PCT) * 100) / 100

def record_offline(conn_id: str):
    now = datetime.now(timezone.utc).isoformat()
    with db() as c:
        c.execute("""
            INSERT INTO offline_tracker (conn_id, first_offline, action_count)
            VALUES (?, ?, 0)
            ON CONFLICT(conn_id) DO NOTHING
        """, (conn_id, now))

def record_action(conn_id: str):
    now = datetime.now(timezone.utc).isoformat()
    with db() as c:
        c.execute("""
            UPDATE offline_tracker
            SET last_action_at=?, action_count=action_count+1
            WHERE conn_id=?
        """, (now, conn_id))

def clear_offline(conn_id: str):
    with db() as c:
        c.execute("DELETE FROM offline_tracker WHERE conn_id=?", (conn_id,))

def get_offline_tracker() -> list:
    with db() as c:
        return c.execute("SELECT * FROM offline_tracker").fetchall()

def save_payment(telegram_id: int, proxy_db_id: int, provider: str, amount: float, proxy_name: str):
    with db() as c:
        c.execute(
            "INSERT INTO payments (telegram_id, proxy_db_id, provider, amount, proxy_name) VALUES (?,?,?,?,?)",
            (telegram_id, proxy_db_id, provider, amount, proxy_name))

def get_payments_this_month() -> list:
    first = datetime.now(timezone.utc).replace(day=1).strftime("%Y-%m-%d")
    with db() as c:
        return c.execute(
            "SELECT * FROM payments WHERE paid_at >= ? ORDER BY paid_at DESC", (first,)
        ).fetchall()

def get_payments_last_month() -> list:
    now        = datetime.now(timezone.utc)
    first_this = now.replace(day=1)
    first_last = (first_this - timedelta(days=1)).replace(day=1)
    with db() as c:
        return c.execute(
            "SELECT * FROM payments WHERE paid_at >= ? AND paid_at < ? ORDER BY paid_at DESC",
            (first_last.strftime("%Y-%m-%d"), first_this.strftime("%Y-%m-%d"))
        ).fetchall()

def get_revenue(rows) -> float:
    return round(sum(r["amount"] for r in rows), 2)

def set_client_notes(tid: int, notes: str):
    with db() as c:
        c.execute("UPDATE clients SET notes=? WHERE telegram_id=?", (notes, tid))

def get_client_notes(tid: int) -> str:
    with db() as c:
        row = c.execute("SELECT notes FROM clients WHERE telegram_id=?", (tid,)).fetchone()
        if row and row["notes"]:
            return row["notes"]
        return ""

def save_pending(payment_id, provider, tid, proxy_db_id, amount, lang, is_reseller):
    with db() as c:
        c.execute("""
            INSERT OR REPLACE INTO pending_payments
            (payment_id, provider, telegram_id, proxy_db_id, amount, lang, is_reseller)
            VALUES (?,?,?,?,?,?,?)
        """, (payment_id, provider, tid, proxy_db_id, amount, lang, 1 if is_reseller else 0))

def get_all_reseller_proxies():
    with db() as c:
        return c.execute("""
            SELECT p.*, cl.lang, cl.tg_username, cl.client_type
            FROM proxies p JOIN clients cl ON cl.telegram_id=p.telegram_id
            WHERE cl.client_type='reseller' AND p.reseller_expiry IS NOT NULL
        """).fetchall()

# ── iProxy ─────────────────────────────────────────────────────────────────────
def proxy_display_name(proxy, is_reseller: bool) -> str:
    """What the client/admin sees for this proxy."""
    if is_reseller:
        return proxy["conn_id"]
    port  = str(proxy["port"]) if proxy["port"] else "?"
    login = proxy["proxy_login"] or ""
    return f":{port}" + (f" ({login})" if login else "")

def iproxy_headers():
    return {"Authorization": f"Bearer {IPROXY_API_KEY}"}

async def iproxy_get(path: str) -> dict | None:
    async with httpx.AsyncClient() as h:
        r = await h.get(f"{IPROXY_BASE}{path}", headers=iproxy_headers(), timeout=10)
    return r.json() if r.status_code == 200 else None

async def iproxy_post(path: str, body: dict) -> dict | None:
    async with httpx.AsyncClient() as h:
        r = await h.post(f"{IPROXY_BASE}{path}", headers=iproxy_headers(), json=body, timeout=10)
    return r.json() if r.status_code == 200 else None

async def get_connection(conn_id: str) -> dict | None:
    return await iproxy_get(f"/connections/{conn_id}")

async def get_proxy_accesses(conn_id: str) -> list:
    data = await iproxy_get(f"/connections/{conn_id}/proxy-access")
    if data:
        accesses = data.get("proxy_accesses", [])
        if accesses:
            logger.debug("proxy_access sample keys: %s", list(accesses[0].keys()))
        return accesses
    return []

async def get_all_connections_status() -> list:
    """Returns list of {id, online_status, online_updated_at, ...} for all connections."""
    data = await iproxy_get("/connections-status")
    if data:
        return data.get("connections", [])
    return []

async def push_command(conn_id: str, action: str, params: dict | None = None) -> bool:
    body: dict = {"action": action}
    if action == "toggle_proxy" and params:
        body["toggle_proxy_params"] = params
    r = await iproxy_post(f"/connections/{conn_id}/command-push", body)
    return r is not None

async def prolongate_plan(conn_id: str, days: int, plan_id: str, expires_at: str) -> dict | None:
    return await iproxy_post(f"/connections/{conn_id}/prolongate-plan", {
        "days": days,
        "active_plan": {"id": plan_id, "expires_at": expires_at},
    })

async def update_proxy_access_expiry(conn_id: str, proxy_id: str, new_expires_at: str) -> bool:
    r = await iproxy_post(f"/connections/{conn_id}/proxy-access/{proxy_id}/update",
                          {"expires_at": new_expires_at})
    return r is not None

async def update_proxy_access_password(conn_id: str, proxy_id: str, login: str, password: str) -> bool:
    r = await iproxy_post(f"/connections/{conn_id}/proxy-access/{proxy_id}/update",
                          {"auth_type": "userpass", "auth": {"login": login, "password": password}})
    return r is not None

def format_expiry(iso: str | None) -> str:
    if not iso:
        return "N/D"
    try:
        if len(iso) == 10:
            dt = datetime.fromisoformat(iso)
        else:
            dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%d/%m/%Y")
    except Exception:
        return iso

# ── PayPal ─────────────────────────────────────────────────────────────────────
async def paypal_token() -> str:
    async with httpx.AsyncClient() as h:
        r = await h.post(f"{PAYPAL_BASE}/v1/oauth2/token",
                         auth=(PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET),
                         data={"grant_type": "client_credentials"}, timeout=10)
    return r.json()["access_token"]

async def paypal_create_order(amount: float, desc: str, meta: dict) -> tuple[str, str]:
    token = await paypal_token()
    async with httpx.AsyncClient() as h:
        r = await h.post(f"{PAYPAL_BASE}/v2/checkout/orders",
                         headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                         json={"intent": "CAPTURE",
                               "purchase_units": [{"amount": {"currency_code": "EUR", "value": f"{amount:.2f}"},
                                                   "description": desc, "custom_id": json.dumps(meta)}],
                               "application_context": {"return_url": f"{BASE_URL}/paypal-success",
                                                       "cancel_url": f"{BASE_URL}/paypal-cancel",
                                                       "brand_name": "GenRDP", "user_action": "PAY_NOW"}},
                         timeout=10)
    data = r.json()
    return data["id"], next(l["href"] for l in data["links"] if l["rel"] == "approve")

async def paypal_capture(order_id: str) -> dict | None:
    token = await paypal_token()
    async with httpx.AsyncClient() as h:
        r = await h.post(f"{PAYPAL_BASE}/v2/checkout/orders/{order_id}/capture",
                         headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                         timeout=10)
    return r.json() if r.status_code in (200, 201) else None

# ── CoinGate ───────────────────────────────────────────────────────────────────
async def coingate_create_order(amount: float, desc: str, meta: dict) -> tuple[str, str]:
    """Returns (order_id, payment_url)"""
    async with httpx.AsyncClient() as h:
        r = await h.post(
            f"{COINGATE_BASE}/v2/orders",
            headers={"Authorization": f"Token {COINGATE_API_KEY}", "Content-Type": "application/json"},
            json={
                "order_id":          str(meta.get("proxy_db_id", "")),
                "price_amount":      f"{amount:.2f}",
                "price_currency":    "EUR",
                "receive_currency":  "USDT",
                "title":             "GenRDP Proxy Renewal",
                "description":       desc,
                "callback_url":      f"{BASE_URL}/coingate-webhook",
                "success_url":       f"{BASE_URL}/payment-success",
                "cancel_url":        f"{BASE_URL}/payment-cancel",
                "token":             json.dumps(meta),   # pass meta through token field
            },
            timeout=10,
        )
    if r.status_code not in (200, 201):
        logger.error("CoinGate order error: %s %s", r.status_code, r.text)
        raise Exception(f"CoinGate error {r.status_code}")
    data = r.json()
    return str(data["id"]), data["payment_url"]

# ── Bot handlers ───────────────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid    = update.effective_user.id
    client = get_client(uid)
    if not client:
        await show_lang_selector(update, ctx, after="not_registered")
        return
    lang = get_lang(client)
    if lang not in SUPPORTED_LANGS:
        await show_lang_selector(update, ctx, after="status")
        return
    await show_status(update, ctx, client)

async def cmd_language(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await show_lang_selector(update, ctx, after="status")

async def cmd_history(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid    = update.effective_user.id
    client = get_client(uid)
    if not client:
        await update.message.reply_text("❌ Account not registered.")
        return
    lang    = get_lang(client)
    proxies = get_proxies(uid)
    is_res  = client["client_type"] == "reseller"
    now     = datetime.now(timezone.utc)

    proxy_lines = []
    for p in proxies:
        name = proxy_display_name(p, is_res)
        if is_res:
            exp = p["reseller_expiry"] or "N/D"
            icon = "🔴" if exp != "N/D" and exp < now.strftime("%Y-%m-%d") else "🟢"
            proxy_lines.append(f"{icon} `{name}` — {format_expiry(exp)}")
        else:
            try:
                accesses = await get_proxy_accesses(p["conn_id"])
                pa = next((a for a in accesses if a.get("id") == p["proxy_id"]), None) if p["proxy_id"] else None
                exp_str = format_expiry(pa.get("expires_at")) if pa and pa.get("expires_at") else "N/D"
                if pa and pa.get("expires_at"):
                    exp_dt     = datetime.fromisoformat(pa["expires_at"].replace("Z", "+00:00"))
                    hours_left = (exp_dt - now).total_seconds() / 3600
                    icon = "🔴" if hours_left < 0 else ("🟡" if hours_left < 48 else "🟢")
                else:
                    icon = "⚪"
            except Exception:
                exp_str = "N/D"
                icon    = "⚪"
            proxy_lines.append(f"{icon} `{name}` — {exp_str}")

    active_text = "\n".join(proxy_lines) if proxy_lines else "_Nessuno_"

    payments = get_client_payments(uid, limit=8)
    if payments:
        pay_lines = [
            f"  {r['paid_at'][:10]} | €{r['amount']:.2f} | {r['provider']}"
            for r in payments
        ]
        pay_text = "\n".join(pay_lines)
    else:
        pay_text = t("history_no_payments", lang)

    text = (
        f"{t('history_title', lang)}\n"
        f"{t('history_active', lang)}\n{active_text}\n"
        f"{t('history_payments', lang)}\n{pay_text}"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def show_lang_selector(update: Update, ctx: ContextTypes.DEFAULT_TYPE, after: str = "status"):
    ctx.user_data["after_lang"] = after
    kb = [[InlineKeyboardButton("🇬🇧 English", callback_data="lang:en"),
           InlineKeyboardButton("🇮🇹 Italiano", callback_data="lang:it")],
          [InlineKeyboardButton("🇨🇳 中文",     callback_data="lang:zh"),
           InlineKeyboardButton("🇷🇺 Русский",  callback_data="lang:ru")]]
    await update.message.reply_text(
        "🌐 Choose language / Scegli lingua / 选择语言 / Выберите язык:",
        reply_markup=InlineKeyboardMarkup(kb))

async def handle_lang(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    await q.answer()
    lang = q.data.split(":")[1]
    uid  = q.from_user.id
    client = get_client(uid)
    if client:
        set_lang(uid, lang)
        client = get_client(uid)
    after = ctx.user_data.pop("after_lang", "status")
    if after == "not_registered" or not client:
        await q.edit_message_text(t("not_registered", lang), parse_mode="Markdown")
        return
    await q.edit_message_text(t("lang_set", lang))
    proxies = get_proxies(uid)
    if not proxies:
        await q.message.reply_text(t("not_registered", lang), parse_mode="Markdown")
        return
    await show_status_message(q.message, ctx, client, proxies)

async def show_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE, client):
    proxies = get_proxies(client["telegram_id"])
    if not proxies:
        lang = get_lang(client)
        await update.message.reply_text(t("not_registered", lang), parse_mode="Markdown")
        return
    await show_status_message(update.message, ctx, client, proxies)

async def show_status_message(msg, ctx: ContextTypes.DEFAULT_TYPE, client, proxies):
    """Main client menu: renew expiring | manage proxies | language."""
    lang   = get_lang(client)
    is_res = client["client_type"] == "reseller"
    now    = datetime.now(timezone.utc)

    # Check for proxies expiring within 48h
    expiring = []
    for p in proxies:
        if is_res:
            exp = p["reseller_expiry"]
            if exp:
                try:
                    exp_dt = datetime.fromisoformat(exp).replace(tzinfo=timezone.utc)
                    if 0 < (exp_dt - now).total_seconds() / 3600 <= 48:
                        expiring.append(p)
                except Exception:
                    pass
        else:
            if not p["proxy_id"]:
                continue
            try:
                accesses = await get_proxy_accesses(p["conn_id"])
                pa = next((a for a in accesses if a.get("id") == p["proxy_id"]), None)
                if pa and pa.get("expires_at"):
                    exp_dt = datetime.fromisoformat(pa["expires_at"].replace("Z", "+00:00"))
                    if 0 < (exp_dt - now).total_seconds() / 3600 <= 48:
                        expiring.append(p)
            except Exception:
                pass

    lang_labels = {"en": "🌐 Language", "it": "🌐 Lingua", "zh": "🌐 语言", "ru": "🌐 Язык"}
    kb = []

    # Row 1: renew expiring (only if any)
    if expiring:
        total = sum(effective_price(p) for p in expiring)
        ids   = ",".join(str(p["id"]) for p in expiring)
        kb.append([InlineKeyboardButton(
            t("renew_all_btn", lang, count=len(expiring), total=total),
            callback_data=f"renewall:{ids}")])

    # Row 2: manage proxies
    kb.append([InlineKeyboardButton(t("btn_manage", lang), callback_data="manage:list")])

    # Row 3: language
    kb.append([InlineKeyboardButton(lang_labels.get(lang, "🌐 Language"),
                                    callback_data="status_lang:0")])

    # Store proxies list in context for manage:list
    ctx.user_data["client_proxies"] = [p["id"] for p in proxies]

    await msg.reply_text(t("main_menu", lang), parse_mode="Markdown",
                         reply_markup=InlineKeyboardMarkup(kb))


async def show_proxy_list(q, ctx, client, proxies, edit: bool = True):
    """Show list of proxies to select for management."""
    lang   = get_lang(client)
    is_res = client["client_type"] == "reseller"
    back_labels = {"en": "⬅️ Back", "it": "⬅️ Indietro", "zh": "⬅️ 返回", "ru": "⬅️ Назад"}
    kb = [[InlineKeyboardButton(proxy_display_name(p, is_res),
                                callback_data=f"proxymenu:{p['id']}")]
          for p in proxies]
    kb.append([InlineKeyboardButton(back_labels.get(lang, "⬅️ Back"),
                                    callback_data="manage:back")])
    text = t("proxy_select", lang)
    if edit:
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
    else:
        await q.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))


async def show_proxy_menu_inline(q, ctx, client, proxy):
    """Show per-proxy action menu: Rinnova | Cambia Password | Indietro."""
    lang   = get_lang(client)
    is_res = client["client_type"] == "reseller"
    name   = proxy_display_name(proxy, is_res)
    base_price = effective_price(proxy)
    back_labels  = {"en": "⬅️ Back", "it": "⬅️ Indietro", "zh": "⬅️ 返回", "ru": "⬅️ Назад"}

    if is_res:
        expiry_str = format_expiry(proxy["reseller_expiry"]) if proxy["reseller_expiry"] else "N/D"
    else:
        try:
            accesses   = await get_proxy_accesses(proxy["conn_id"])
            pa         = next((a for a in accesses if a.get("id") == proxy["proxy_id"]), None) if proxy["proxy_id"] else None
            expiry_str = format_expiry(pa.get("expires_at")) if pa and pa.get("expires_at") else "N/D"
        except Exception:
            expiry_str = "N/D"

    # Action buttons: what do you want to do?
    kb = [
        [InlineKeyboardButton(t("btn_renew_proxy", lang), callback_data=f"proxyrenew:{proxy['id']}")],
    ]
    if not is_res and proxy["proxy_id"]:
        kb.append([InlineKeyboardButton(t("btn_change_password", lang),
                                        callback_data=f"changepw:{proxy['id']}")])
    kb.append([InlineKeyboardButton(back_labels.get(lang, "⬅️ Back"),
                                    callback_data="manage:list")])

    await q.edit_message_text(
        t("proxy_action_menu", lang, name=name, expiry=expiry_str),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb))


async def show_proxy_payment(q, ctx, client, proxy):
    """Show payment methods for a specific proxy (called after tapping Rinnova)."""
    lang   = get_lang(client)
    is_res = client["client_type"] == "reseller"
    name   = proxy_display_name(proxy, is_res)
    base_price = effective_price(proxy)
    ppp        = pp_price(base_price)
    plan_label = proxy["plan_type"].capitalize()
    back_labels  = {"en": "⬅️ Back", "it": "⬅️ Indietro", "zh": "⬅️ 返回", "ru": "⬅️ Назад"}

    if is_res:
        expiry_str = format_expiry(proxy["reseller_expiry"]) if proxy["reseller_expiry"] else "N/D"
        expires_at = plan_id = conn_exp = None
    else:
        try:
            accesses   = await get_proxy_accesses(proxy["conn_id"])
            pa         = next((a for a in accesses if a.get("id") == proxy["proxy_id"]), None) if proxy["proxy_id"] else None
            expires_at = pa.get("expires_at") if pa else None
            expiry_str = format_expiry(expires_at)
            conn       = await get_connection(proxy["conn_id"])
            plan_info  = (conn.get("plan_info") or {}) if conn else {}
            active_pl  = plan_info.get("active_plan") or {}
            plan_id    = active_pl.get("id")
            conn_exp   = active_pl.get("expires_at")
        except Exception:
            expiry_str = "N/D"
            expires_at = plan_id = conn_exp = None

    ctx.user_data.update({
        "proxy_db_id":  proxy["id"],
        "lang":         lang,
        "is_reseller":  is_res,
        "plan_id":      plan_id if not is_res else None,
        "conn_expires": conn_exp if not is_res else None,
        "proxy_expires": expires_at if not is_res else None,
    })

    kb = [
        [InlineKeyboardButton(t("btn_card",   lang, price=base_price), callback_data=f"pay:card:{proxy['id']}")],
        [InlineKeyboardButton(t("btn_paypal", lang, price=ppp),        callback_data=f"pay:paypal:{proxy['id']}")],
        [InlineKeyboardButton(t("btn_crypto", lang, price=base_price), callback_data=f"pay:crypto:{proxy['id']}")],
        [InlineKeyboardButton(back_labels.get(lang, "⬅️ Back"),
                              callback_data=f"proxymenu:{proxy['id']}")],
    ]

    await q.edit_message_text(
        t("status_msg", lang, name=name, plan=plan_label, price=base_price, expiry=expiry_str),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb))


async def handle_status_lang(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Language picker accessible from the proxy selector screen."""
    q = update.callback_query
    await q.answer()
    proxy_db_id = int(q.data.split(":")[1])
    # 0 means called from selector, not from a specific proxy status
    ctx.user_data["after_lang_proxy"] = proxy_db_id if proxy_db_id != 0 else None
    kb = [[InlineKeyboardButton("🇬🇧 English", callback_data="slang:en"),
           InlineKeyboardButton("🇮🇹 Italiano", callback_data="slang:it")],
          [InlineKeyboardButton("🇨🇳 中文",     callback_data="slang:zh"),
           InlineKeyboardButton("🇷🇺 Русский",  callback_data="slang:ru")]]
    await q.edit_message_text(
        "🌐 Choose language / Scegli lingua / 选择语言 / Выберите язык:",
        reply_markup=InlineKeyboardMarkup(kb))

async def handle_slang(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle language selection from proxy selector screen."""
    q    = update.callback_query
    await q.answer()
    lang = q.data.split(":")[1]
    uid  = q.from_user.id
    set_lang(uid, lang)
    client = get_client(uid)
    proxy_db_id = ctx.user_data.pop("after_lang_proxy", None)
    if proxy_db_id:
        proxy = get_proxy_by_id(proxy_db_id)
        if proxy and client:
            await show_proxy_status_inline(q, ctx, client, proxy)
            return
    # Re-show main menu in new language
    if client:
        proxies = get_proxies(uid)
        if proxies:
            await q.edit_message_text(t("lang_set", lang))
            await show_status_message(q.message, ctx, client, proxies)
            return
    await q.edit_message_text(t("lang_set", lang))

async def handle_renewall(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle renew-all button — create a single aggregated payment."""
    q   = update.callback_query
    await q.answer()
    uid = q.from_user.id
    client = get_client(uid)
    if not client:
        await q.edit_message_text("❌ Session expired.")
        return
    lang = get_lang(client)

    ids_str    = q.data.split(":", 1)[1]   # "renewall:1,2,3"
    proxy_ids  = [int(x) for x in ids_str.split(",")]
    proxies    = [get_proxy_by_id(pid) for pid in proxy_ids]
    proxies    = [p for p in proxies if p]
    if not proxies:
        await q.edit_message_text("❌ Proxies not found.")
        return

    total      = sum(effective_price(p) for p in proxies)
    ppp        = pp_price(total)
    is_res_cl  = client["client_type"] == "reseller" if client else False
    names      = ", ".join(proxy_display_name(p, is_res_cl) for p in proxies)
    desc       = f"GenRDP – Rinnovo {len(proxies)} proxy"

    # Store multi-proxy context
    ctx.user_data["renewall_ids"] = proxy_ids
    ctx.user_data["lang"]         = lang

    await q.edit_message_text(t("generating", lang))

    kb_card = InlineKeyboardMarkup([
        [InlineKeyboardButton(t("btn_pay_card",   lang), url="PLACEHOLDER")],
        [InlineKeyboardButton(t("btn_pay_paypal", lang), url="PLACEHOLDER")],
        [InlineKeyboardButton(t("btn_pay_crypto", lang), url="PLACEHOLDER")],
    ])

    await q.edit_message_text(
        f"🔄 *Rinnovo aggregato*\n{names}\n"
        f"Scegli il metodo di pagamento:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(t("btn_card",   lang, price=total), callback_data=f"payall:card:{ids_str}")],
            [InlineKeyboardButton(t("btn_paypal", lang, price=ppp),   callback_data=f"payall:paypal:{ids_str}")],
            [InlineKeyboardButton(t("btn_crypto", lang, price=total), callback_data=f"payall:crypto:{ids_str}")],
        ]))

async def handle_payall(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Create aggregated payment for multiple proxies."""
    q      = update.callback_query
    await q.answer()
    parts    = q.data.split(":", 2)   # payall:card:1,2,3
    provider = parts[1]
    ids_str  = parts[2]
    uid      = q.from_user.id
    client   = get_client(uid)
    lang     = get_lang(client) if client else "en"

    proxy_ids = [int(x) for x in ids_str.split(",")]
    proxies   = [get_proxy_by_id(pid) for pid in proxy_ids]
    proxies   = [p for p in proxies if p]
    if not proxies:
        await q.edit_message_text("❌ Proxies not found.")
        return

    total = sum(effective_price(p) for p in proxies)
    is_res_cl2 = client["client_type"] == "reseller" if client else False
    names = ", ".join(proxy_display_name(p, is_res_cl2) for p in proxies)
    desc  = f"GenRDP – {len(proxies)} proxy renewal"
    meta  = {
        "telegram_id":  uid,
        "proxy_db_ids": ids_str,          # comma-separated
        "amount":       total,
        "lang":         lang,
        "is_multi":     "1",
    }

    await q.edit_message_text(t("generating", lang),
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("↩️ Back", callback_data=f"renewall:{ids_str}")
        ]]))

    try:
        if provider == "card":
            session = stripe.checkout.Session.create(
                line_items=[{"price_data": {
                    "currency": "eur",
                    "product_data": {"name": desc},
                    "unit_amount": int(total * 100),
                }, "quantity": 1}],
                mode="payment",
                payment_method_types=["card", "alipay"],
                success_url=f"{BASE_URL}/payment-success?session_id={{CHECKOUT_SESSION_ID}}",
                cancel_url=f"{BASE_URL}/payment-cancel",
                metadata={k: str(v) for k, v in meta.items()},
            )
            pay_url = session["url"]
            # Store pending for each proxy
            for p in proxies:
                save_pending(f"{session['id']}_{p['id']}", "stripe", uid,
                             p["id"], effective_price(p), lang, False)

        elif provider == "paypal":
            ppp = pp_price(total)
            order_id, pay_url = await paypal_create_order(ppp, desc, meta)
            for p in proxies:
                save_pending(f"{order_id}_{p['id']}", "paypal", uid,
                             p["id"], effective_price(p), lang, False)

        else:  # crypto
            order_id, pay_url = await coingate_create_order(total, desc, meta)
            for p in proxies:
                save_pending(f"{order_id}_{p['id']}", "coingate", uid,
                             p["id"], effective_price(p), lang, False)

        ppp_val = pp_price(total)
        if provider == "paypal":
            text = t("pay_ready_paypal", lang, base=total, fee=round(ppp_val-total,2), total=ppp_val)
        elif provider == "crypto":
            text = t("pay_ready_crypto", lang, price=total)
        else:
            text = t("renew_all_ready_card", lang, count=len(proxies), total=total)

        btn_label = {"card": t("btn_pay_card", lang), "paypal": t("btn_pay_paypal", lang),
                     "crypto": t("btn_pay_crypto", lang)}[provider]
        await q.edit_message_text(text, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(btn_label, url=pay_url)],
                [InlineKeyboardButton("↩️ Back", callback_data=f"renewall:{ids_str}")],
            ]))

    except Exception as e:
        logger.error("payall error (%s): %s", provider, e)
        await q.edit_message_text(t("pay_error", lang),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("↩️ Back", callback_data=f"renewall:{ids_str}")
            ]]))

async def handle_manage(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle manage:list and manage:back callbacks."""
    q   = update.callback_query
    await q.answer()
    uid = q.from_user.id
    client = get_client(uid)
    if not client:
        await q.edit_message_text("❌ Session expired. Use /start.")
        return
    lang    = get_lang(client)
    proxies = get_proxies(uid)
    if not proxies:
        await q.edit_message_text(t("not_registered", lang), parse_mode="Markdown")
        return

    action = q.data.split(":")[1]   # "list" or "back"

    if action == "back":
        # Return to main menu
        await show_status_message(q.message, ctx, client, proxies)
    else:
        # Show proxy list
        if len(proxies) == 1:
            await show_proxy_menu_inline(q, ctx, client, proxies[0])
        else:
            await show_proxy_list(q, ctx, client, proxies, edit=True)


async def handle_proxymenu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle proxymenu:<id> — show per-proxy action menu."""
    q = update.callback_query
    await q.answer()
    proxy_db_id = int(q.data.split(":")[1])
    proxy  = get_proxy_by_id(proxy_db_id)
    client = get_client(q.from_user.id)
    if not proxy or not client:
        await q.edit_message_text("❌ Session expired. Use /start.")
        return
    await show_proxy_menu_inline(q, ctx, client, proxy)


async def handle_proxyrenew(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle proxyrenew:<id> — show payment methods for this proxy."""
    q = update.callback_query
    await q.answer()
    proxy_db_id = int(q.data.split(":")[1])
    proxy  = get_proxy_by_id(proxy_db_id)
    client = get_client(q.from_user.id)
    if not proxy or not client:
        await q.edit_message_text("❌ Session expired. Use /start.")
        return
    await show_proxy_payment(q, ctx, client, proxy)


async def handle_back_selector(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Back button from payment screen → proxy action menu."""
    q   = update.callback_query
    await q.answer()
    proxy_db_id = int(q.data.split(":")[1])
    proxy  = get_proxy_by_id(proxy_db_id)
    client = get_client(q.from_user.id)
    if not proxy or not client:
        await q.edit_message_text("❌ Session expired. Use /start.")
        return
    await show_proxy_menu_inline(q, ctx, client, proxy)

async def handle_back_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Back button — edit the payment message back to status screen."""
    q = update.callback_query
    await q.answer()
    proxy_db_id = int(q.data.split(":")[1])
    proxy  = get_proxy_by_id(proxy_db_id)
    client = get_client(q.from_user.id)
    if not proxy or not client:
        await q.edit_message_text("❌ Session expired. Use /start.")
        return
    try:
        text, kb, lang = await _build_status_text_kb(client, proxy, ctx)
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
    except Exception as e:
        logger.error("handle_back_status error: %s", e)
        await q.message.reply_text("↩️ /start")

async def handle_selproxy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    proxy_db_id = int(q.data.split(":")[1])
    proxy  = get_proxy_by_id(proxy_db_id)
    client = get_client(q.from_user.id)
    if not proxy or not client:
        await q.edit_message_text("❌ Proxy not found.")
        return
    await show_proxy_menu_inline(q, ctx, client, proxy)

async def _build_status_text_kb(client, proxy, ctx):
    """Shared builder for status text + keyboard."""
    lang       = get_lang(client)
    is_res     = client["client_type"] == "reseller"
    base_price = effective_price(proxy)
    ppp        = pp_price(base_price)
    plan_label = proxy["plan_type"].capitalize()
    name       = proxy_display_name(proxy, is_res)

    if is_res:
        expiry_str   = proxy["reseller_expiry"] or "N/D"
        plan_id      = None
        expires_at   = None
        conn_expires = None
    else:
        accesses = await get_proxy_accesses(proxy["conn_id"])
        pa = next((a for a in accesses if a.get("id") == proxy["proxy_id"]), None) if proxy["proxy_id"] else None
        expires_at   = pa.get("expires_at") if pa else None
        expiry_str   = format_expiry(expires_at)
        conn         = await get_connection(proxy["conn_id"])
        plan_info    = (conn.get("plan_info") or {}) if conn else {}
        active_pl    = plan_info.get("active_plan") or {}
        plan_id      = active_pl.get("id")
        conn_expires = active_pl.get("expires_at")

    ctx.user_data.update({
        "proxy_db_id":  proxy["id"],
        "lang":         lang,
        "is_reseller":  is_res,
        "plan_id":      plan_id if not is_res else None,
        "conn_expires": conn_expires if not is_res else None,
        "proxy_expires": expires_at if not is_res else None,
    })

    kb = [
        [InlineKeyboardButton(t("btn_card",   lang, price=base_price), callback_data=f"pay:card:{proxy['id']}")],
        [InlineKeyboardButton(t("btn_paypal", lang, price=ppp),        callback_data=f"pay:paypal:{proxy['id']}")],
        [InlineKeyboardButton(t("btn_crypto", lang, price=base_price), callback_data=f"pay:crypto:{proxy['id']}")],
    ]
    if not is_res and proxy["proxy_id"]:
        kb.append([InlineKeyboardButton(t("btn_change_password", lang),
                                        callback_data=f"changepw:{proxy['id']}")])
    # Back button — returns to proxy selector (useful when client has multiple proxies)
    kb.append([InlineKeyboardButton("↩️ " + {"en":"Back","it":"Indietro","zh":"返回","ru":"Назад"}.get(lang,"Back"),
                                    callback_data=f"back_selector:{proxy['telegram_id']}")])
    text = t("status_msg", lang, name=name, plan=plan_label, price=base_price, expiry=expiry_str)
    return text, kb, lang

async def show_proxy_status_inline(q, ctx, client, proxy):
    """Edit existing message in-place (for Back button)."""
    try:
        text, kb, lang = await _build_status_text_kb(client, proxy, ctx)
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
    except Exception as e:
        logger.error("show_proxy_status_inline error: %s", e)
        # Fallback: send a new message
        await q.message.reply_text(
            "↩️ Use the buttons above or press /start",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Refresh", callback_data=f"back_status:{proxy['id']}")
            ]]))

async def show_proxy_status(msg, ctx: ContextTypes.DEFAULT_TYPE, client, proxy):
    text, kb, lang = await _build_status_text_kb(client, proxy, ctx)
    loading = await msg.reply_text(t("loading", lang))
    await loading.edit_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def handle_pay(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q        = update.callback_query
    await q.answer()
    parts    = q.data.split(":")   # pay:card:123 or pay:card (legacy)
    provider = parts[1]
    uid      = q.from_user.id
    client   = get_client(uid)
    lang     = get_lang(client) if client else "en"

    # Read proxy_db_id from callback data (reliable) or fallback to user_data
    if len(parts) >= 3:
        proxy_db_id = int(parts[2])
    else:
        proxy_db_id = ctx.user_data.get("proxy_db_id")

    if not proxy_db_id:
        await q.edit_message_text(t("session_expired", lang))
        return

    proxy = get_proxy_by_id(proxy_db_id)
    if not proxy:
        await q.edit_message_text(t("session_expired", lang))
        return

    is_res = client["client_type"] == "reseller" if client else False
    lang   = ctx.user_data.get("lang", lang)

    base_price = effective_price(proxy)
    name       = proxy_display_name(proxy, is_res)
    desc       = f"GenRDP – 30d renewal ({name})"
    meta       = {"proxy_db_id": proxy_db_id, "telegram_id": uid,
                  "amount": base_price, "lang": lang, "is_reseller": 1 if is_res else 0}

    await q.edit_message_text(
        t("generating", lang),
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("↩️ Back", callback_data=f"back_status:{proxy_db_id}")
        ]]))

    try:
        if provider == "card":
            session = stripe.checkout.Session.create(
                line_items=[{"price_data": {"currency": "eur",
                                            "product_data": {"name": f"GenRDP – 30d renewal ({name})"},
                                            "unit_amount": int(base_price * 100)},
                             "quantity": 1}],
                mode="payment",
                payment_method_types=["card", "alipay"],
                success_url=f"{BASE_URL}/payment-success?session_id={{CHECKOUT_SESSION_ID}}",
                cancel_url=f"{BASE_URL}/payment-cancel",
                metadata={k: str(v) for k, v in meta.items()},
            )
            pay_url = session["url"]
            save_pending(session["id"], "stripe", uid, proxy_db_id, base_price, lang, is_res)
            await q.edit_message_text(
                t("pay_ready_card", lang, price=base_price), parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(t("btn_pay_card", lang), url=pay_url),
                ], [
                    InlineKeyboardButton("↩️ Back", callback_data=f"back_status:{proxy_db_id}"),
                ]]))

        elif provider == "paypal":
            total = pp_price(base_price)
            fee   = round(total - base_price, 2)
            order_id, pay_url = await paypal_create_order(total, desc, meta)
            save_pending(order_id, "paypal", uid, proxy_db_id, base_price, lang, is_res)
            await q.edit_message_text(
                t("pay_ready_paypal", lang, base=base_price, fee=fee, total=total),
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(t("btn_pay_paypal", lang), url=pay_url),
                ], [
                    InlineKeyboardButton("↩️ Back", callback_data=f"back_status:{proxy_db_id}"),
                ]]))

        else:  # crypto
            order_id, pay_url = await coingate_create_order(base_price, desc, meta)
            save_pending(order_id, "coingate", uid, proxy_db_id, base_price, lang, is_res)
            await q.edit_message_text(
                t("pay_ready_crypto", lang, price=base_price), parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(t("btn_pay_crypto", lang), url=pay_url),
                ], [
                    InlineKeyboardButton("↩️ Back", callback_data=f"back_status:{proxy_db_id}"),
                ]]))

    except Exception as e:
        logger.error("Payment error (%s): %s", provider, e)
        await q.edit_message_text(
            t("pay_error", lang),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("↩️ Back", callback_data=f"back_status:{proxy_db_id}")
            ]]))

# ── Core renewal ───────────────────────────────────────────────────────────────
async def do_renewal(bot, proxy_db_id: int, amount: float, lang: str, is_reseller: bool,
                     plan_id: str | None = None, conn_expires: str | None = None,
                     proxy_expires: str | None = None, provider: str = 'unknown'):
    proxy  = get_proxy_by_id(proxy_db_id)
    client = get_proxy_client(proxy_db_id)
    if not proxy or not client:
        logger.error("do_renewal: proxy %s not found", proxy_db_id)
        return

    tid    = client["telegram_id"]
    name   = proxy["proxy_name"] or proxy["conn_id"]
    port   = proxy["port"]
    client_name = client["tg_username"] or f"ID {tid}"

    if is_reseller:
        new_exp = extend_reseller_expiry(proxy_db_id)
        await bot.send_message(chat_id=tid,
            text=t("renewal_ok_reseller", lang, name=name, expiry=new_exp),
            parse_mode="Markdown")
        save_payment(tid, proxy_db_id, provider, amount, name)
        await bot.send_message(chat_id=NOTIFY_USERNAME,
            text=f"🔔 *Rinnovo Reseller*\n👤 Cliente: `{client_name}`\n📡 `{name}`\n🔌 Porta: `{port or 'N/D'}`\n💶 €{amount:.2f}\n📅 Nuova scadenza: {new_exp}",
            parse_mode="Markdown")
        return

    # Normal client: ONLY update proxy_access expires_at
    # (connection has auto-renewal on iProxy side)
    success = False
    new_expiry = None

    if proxy["proxy_id"] and proxy_expires:
        now = datetime.now(timezone.utc)
        try:
            base = datetime.fromisoformat(proxy_expires.replace("Z", "+00:00"))
            if base < now:
                base = now
        except Exception:
            base = now
        new_expiry = (base + timedelta(days=RENEWAL_DAYS)).isoformat()
        ok = await update_proxy_access_expiry(proxy["conn_id"], proxy["proxy_id"], new_expiry)
        success = ok
    elif proxy["proxy_id"] and not proxy_expires:
        # No current expiry — set from now + 30 days
        new_expiry = (datetime.now(timezone.utc) + timedelta(days=RENEWAL_DAYS)).isoformat()
        ok = await update_proxy_access_expiry(proxy["conn_id"], proxy["proxy_id"], new_expiry)
        success = ok

    if success and new_expiry:
        await bot.send_message(chat_id=tid,
            text=t("renewal_ok", lang, name=name, port=port or "N/D", expiry=format_expiry(new_expiry)),
            parse_mode="Markdown")
        save_payment(tid, proxy_db_id, provider, amount, name)
        await bot.send_message(chat_id=NOTIFY_USERNAME,
            text=f"🔔 *Nuovo rinnovo*\n👤 Cliente: `{client_name}`\n📡 `{name}`\n🔌 Porta: `{port or 'N/D'}`\n💶 €{amount:.2f}\n📅 Nuova scadenza: {format_expiry(new_expiry)}",
            parse_mode="Markdown")
    else:
        await bot.send_message(chat_id=tid, text=t("renewal_fail", lang))
        await bot.send_message(chat_id=NOTIFY_USERNAME,
            text=f"🚨 *RINNOVO MANUALE*\n👤 Cliente: `{client_name}`\nTG: `{tid}`\nProxy DB ID: `{proxy_db_id}`\n`{name}`\n€{amount:.2f}",
            parse_mode="Markdown")
        logger.error("Manual renewal needed: proxy_db_id=%s tid=%s", proxy_db_id, tid)

async def handle_changepw(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    proxy_db_id = int(q.data.split(":")[1])
    client = get_client(q.from_user.id)
    proxy  = get_proxy_by_id(proxy_db_id)
    if not client or not proxy:
        return
    lang = get_lang(client)
    name = proxy["proxy_name"] or proxy["conn_id"]
    ctx.user_data["changepw"] = {"proxy_db_id": proxy_db_id, "lang": lang}
    ctx.user_data["changepw_waiting"] = True
    await q.edit_message_text(
        t("change_password", lang, name=name),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Cancel / Annulla", callback_data=f"changepw_cancel:{proxy_db_id}")
        ]]))

async def handle_changepw_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    ctx.user_data.pop("changepw", None)
    ctx.user_data.pop("changepw_waiting", None)
    proxy_db_id = int(q.data.split(":")[1])
    proxy  = get_proxy_by_id(proxy_db_id)
    client = get_client(q.from_user.id)
    if proxy and client:
        await show_proxy_status_inline(q, ctx, client, proxy)

async def handle_changepw_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.user_data.get("changepw_waiting"):
        return False  # signal: not handled
    pw_data     = ctx.user_data.get("changepw", {})
    proxy_db_id = pw_data.get("proxy_db_id")
    lang        = pw_data.get("lang", "en")
    new_pw      = update.message.text.strip()
    if len(new_pw) < 6:
        await update.message.reply_text(t("password_short", lang), parse_mode="Markdown")
        return True
    proxy = get_proxy_by_id(proxy_db_id)
    client = get_client(update.effective_user.id)
    if not proxy or not client or not proxy["proxy_id"]:
        await update.message.reply_text("❌ Error.")
        return True
    login = proxy["proxy_login"] or proxy["proxy_name"]
    ok = await update_proxy_access_password(proxy["conn_id"], proxy["proxy_id"], login, new_pw)
    ctx.user_data.pop("changepw", None)
    ctx.user_data.pop("changepw_waiting", None)
    name = proxy["proxy_name"] or proxy["conn_id"]
    if ok:
        await update.message.reply_text(t("password_ok", lang, name=name), parse_mode="Markdown")
    else:
        await update.message.reply_text(t("password_fail", lang), parse_mode="Markdown")
    # Auto-show status again
    await show_proxy_status(update.message, ctx, client, proxy)
    return True

def fire_renewal(meta: dict, provider: str):
    try:
        lang   = meta.get("lang", "en")
        is_res = str(meta.get("is_reseller", "0")) == "1"

        # Multi-proxy aggregated payment
        if meta.get("is_multi") == "1":
            ids_str   = meta["proxy_db_ids"]
            proxy_ids = [int(x) for x in ids_str.split(",")]
            amount    = float(meta["amount"])
            per_proxy = amount / len(proxy_ids) if proxy_ids else amount
            for pid in proxy_ids:
                threading.Thread(
                    target=lambda p=pid, a=per_proxy: asyncio.run(
                        do_renewal(_app.bot, p, a, lang, False,
                                   provider=provider)
                    ), daemon=True).start()
            return

        # Single proxy
        proxy_db_id = int(meta["proxy_db_id"])
        amount      = float(meta["amount"])
        plan_id     = meta.get("plan_id")
        conn_exp    = meta.get("conn_expires")
        proxy_exp   = meta.get("proxy_expires")
    except (KeyError, ValueError) as e:
        logger.error("fire_renewal bad meta: %s — %s", meta, e)
        return
    threading.Thread(
        target=lambda: asyncio.run(
            do_renewal(_app.bot, proxy_db_id, amount, lang, is_res, plan_id, conn_exp, proxy_exp, provider)
        ), daemon=True).start()

# ── Admin panel ────────────────────────────────────────────────────────────────
def is_admin(uid: int) -> bool:
    return uid in ADMIN_TELEGRAM_IDS

def client_label(cl) -> str:
    name = cl["tg_username"] or f"ID {cl['telegram_id']}"
    icon = "👔" if cl["client_type"] == "reseller" else "👤"
    return f"{icon} {name}"

async def cmd_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    await show_admin_menu(update.message)

async def cmd_revenue(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    this_rows = get_payments_this_month()
    last_rows = get_payments_last_month()
    now = datetime.now(timezone.utc)
    m_this = now.strftime('%B %Y')
    m_last = ((now.replace(day=1) - timedelta(days=1)).replace(day=1)).strftime('%B %Y')
    await update.message.reply_text(
        f'💰 *{m_this}:* €{get_revenue(this_rows):.2f} ({len(this_rows)} rinnovi)\n'
        f'💰 *{m_last}:* €{get_revenue(last_rows):.2f} ({len(last_rows)} rinnovi)',
        parse_mode='Markdown')

async def show_admin_menu(msg, edit=False):
    text = "🔧 *Admin Panel GenRDP*\nCosa vuoi fare?"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 Clienti",      callback_data="adm:clients")],
        [InlineKeyboardButton("➕ Nuovo cliente", callback_data="adm:newclient")],
        [InlineKeyboardButton("📋 Scadenze",      callback_data="adm:expiries")],
        [InlineKeyboardButton("🔧 Strumenti",     callback_data="adm:tools")],
    ])
    if edit:
        await msg.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
    else:
        await msg.reply_text(text, parse_mode="Markdown", reply_markup=kb)

async def handle_admin_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q   = update.callback_query
    await q.answer()
    uid = q.from_user.id
    if not is_admin(uid):
        return
    d = q.data

    if d == "adm:menu":
        await show_admin_menu(q, edit=True)
    elif d == "adm:clients":
        await adm_clients(q)
    elif d == "adm:newclient":
        await adm_new_client_start(q, ctx)
    elif d == "adm:expiries":
        await adm_expiries(q)
    elif d == "adm:tools":
        await adm_tools(q)
    elif d == "adm:cancel":
        ctx.user_data.pop("adm", None)
        ctx.user_data.pop("adm_text", None)
        await show_admin_menu(q, edit=True)
    elif d.startswith("adm:client:"):
        await adm_show_client(q, ctx, int(d.split(":")[2]))
    elif d.startswith("adm:addproxy:"):
        await adm_addproxy_start(q, ctx, int(d.split(":")[2]))
    elif d.startswith("adm:plan:"):
        # adm:plan:<tid>:<conn_id>:<proxy_id>:<plan>
        parts = d.split(":")
        tid, conn_id, proxy_id, plan = int(parts[2]), parts[3], parts[4], parts[5]
        adm_st = ctx.user_data.setdefault("adm", {})
        adm_st.update({"tid": tid, "conn_id": conn_id, "proxy_id": proxy_id, "plan": plan})
        client = get_client(tid)
        if plan == "override":
            adm_st["step"] = "override_price"
            ctx.user_data["adm_text"] = True
            await q.edit_message_text(
                "💶 Inserisci il prezzo personalizzato (es. `52.5`):",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Annulla", callback_data="adm:cancel")]]))
        elif client and client["client_type"] == "reseller":
            adm_st["step"] = "expiry"
            ctx.user_data["adm_text"] = True
            await q.edit_message_text(
                f"✅ Piano: *{plan.capitalize()}* (€{DEFAULT_PRICES[plan]:.2f})\n\n📅 Inserisci scadenza (`YYYY-MM-DD`):",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Annulla", callback_data="adm:cancel")]]))
        else:
            await adm_finalize_proxy(q, ctx, tid, conn_id, proxy_id, plan, None, None)
    elif d.startswith("adm:editnote:"):
        tid = int(d.split(":")[2])
        notes = get_client_notes(tid)
        ctx.user_data["adm"] = {"step": "edit_note", "tid": tid}
        ctx.user_data["adm_text"] = True
        current = f"Nota attuale: _{notes}_\n\n" if notes else ""
        await q.edit_message_text(
            f"📝 {current}Scrivi la nuova nota per questo cliente\n_(invia `-` per cancellare la nota)_:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Annulla", callback_data=f"adm:client:{tid}")]]))
    elif d == "adm:revenue":
        await adm_revenue(q)
    elif d == "adm:broadcast":
        ctx.user_data["adm"] = {"step": "broadcast_target"}
        ctx.user_data["adm_text"] = False
        await q.edit_message_text(
            "📢 *Newsletter / Broadcast*\nA chi vuoi inviare il messaggio?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("👥 Tutti i clienti",  callback_data="adm:bcast:all")],
                [InlineKeyboardButton("👤 Solo Client",      callback_data="adm:bcast:client")],
                [InlineKeyboardButton("👔 Solo Reseller",    callback_data="adm:bcast:reseller")],
                [InlineKeyboardButton("❌ Annulla",          callback_data="adm:cancel")],
            ]))
    elif d.startswith("adm:bcast:") and not d.startswith("adm:bcastconfirm:"):
        target = d.split(":")[2]
        ctx.user_data["adm"] = {"step": "broadcast_text", "target": target}
        ctx.user_data["adm_text"] = True
        labels = {"all": "tutti i clienti", "client": "i Client", "reseller": "i Reseller"}
        await q.edit_message_text(
            f"📢 Broadcast a *{labels[target]}*\nScrivi il messaggio (supporta _Markdown_):",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Annulla", callback_data="adm:cancel")]]))
    elif d.startswith("adm:bcastconfirm:"):
        target = d.split(":")[2]
        await q.edit_message_text("⏳ Invio in corso...")
        await _do_broadcast(ctx.bot, q, target)
    elif d.startswith("adm:removeclient:"):
        tid = int(d.split(":")[2])
        cl  = get_client(tid)
        name = client_label(cl) if cl else f"ID {tid}"
        proxies = get_proxies(tid)
        proxy_count = len(proxies)
        await q.edit_message_text(
            f"🚫 Rimuovere *{name}* e tutti i suoi *{proxy_count} proxy*?\n\n"
            f"_Questa azione non può essere annullata. I proxy restano attivi su iProxy._",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Sì, rimuovi cliente", callback_data=f"adm:doremoveclient:{tid}")],
                [InlineKeyboardButton("❌ Annulla",             callback_data=f"adm:client:{tid}")],
            ]))
    elif d.startswith("adm:doremoveclient:"):
        tid  = int(d.split(":")[2])
        cl   = get_client(tid)
        name = client_label(cl) if cl else f"ID {tid}"
        remove_client(tid)
        await q.edit_message_text(f"✅ Cliente *{name}* rimosso.", parse_mode="Markdown")
        # Auto-show clients list
        await adm_clients(q)
    elif d.startswith("adm:ovtype:"):
        # adm:ovtype:<tid>:<conn_id>:<proxy_id>:<plan_type>
        parts = d.split(":")
        tid, conn_id, proxy_id, plan_type = int(parts[2]), parts[3], parts[4], parts[5]
        adm_st = ctx.user_data.setdefault("adm", {})
        price_override = adm_st.get("price_override")
        client = get_client(tid)
        if client and client["client_type"] == "reseller":
            adm_st.update({"tid": tid, "conn_id": conn_id, "proxy_id": proxy_id,
                           "plan": plan_type, "step": "expiry"})
            ctx.user_data["adm_text"] = True
            await q.edit_message_text(
                f"📅 Inserisci scadenza reseller (`YYYY-MM-DD`):",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Annulla", callback_data="adm:cancel")]]))
        else:
            await adm_finalize_proxy(q, ctx, tid, conn_id, proxy_id, plan_type, price_override, None)
    elif d.startswith("adm:removeproxy:"):

        tid = int(d.split(":")[2])
        await adm_remove_proxy_list(q, ctx, tid)
    elif d.startswith("adm:confirmremove:"):
        parts = d.split(":")
        proxy_db_id, tid = int(parts[2]), int(parts[3])
        px = get_proxy_by_id(proxy_db_id)
        name = px["proxy_name"] if px else str(proxy_db_id)
        await q.edit_message_text(
            f"⚠️ Rimuovere *{name}* dal bot?\n_(Il proxy resta attivo su iProxy)_",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Sì, rimuovi", callback_data=f"adm:doremove:{proxy_db_id}:{tid}")],
                [InlineKeyboardButton("❌ Annulla",     callback_data=f"adm:client:{tid}")],
            ]))
    elif d.startswith("adm:doremove:"):
        parts = d.split(":")
        proxy_db_id, tid = int(parts[2]), int(parts[3])
        px = get_proxy_by_id(proxy_db_id)
        name = px["proxy_name"] if px else str(proxy_db_id)
        remove_proxy(proxy_db_id)
        # Auto-show client page
        await adm_show_client(q, ctx, tid)
    elif d.startswith("adm:moveproxy:"):
        tid = int(d.split(":")[2])
        await adm_move_proxy_list(q, ctx, tid)
    elif d.startswith("adm:movepick:"):
        parts = d.split(":")
        proxy_db_id, tid = int(parts[2]), int(parts[3])
        ctx.user_data["adm"] = {"step": "move_tid", "proxy_db_id": proxy_db_id, "from_tid": tid}
        ctx.user_data["adm_text"] = True
        px = get_proxy_by_id(proxy_db_id)
        await q.edit_message_text(
            f"🔄 Sposto *{px['proxy_name'] if px else proxy_db_id}*\n\nInvia il Telegram ID destinatario:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Annulla", callback_data=f"adm:client:{tid}")]]))
    elif d.startswith("adm:overprice:"):
        tid = int(d.split(":")[2])
        await adm_overprice_list(q, ctx, tid)
    elif d.startswith("adm:overprice2:"):
        parts = d.split(":")
        proxy_db_id, tid = int(parts[2]), int(parts[3])
        ctx.user_data["adm"] = {"step": "set_price", "proxy_db_id": proxy_db_id, "tid": tid}
        ctx.user_data["adm_text"] = True
        px = get_proxy_by_id(proxy_db_id)
        await q.edit_message_text(
            f"💶 Prezzo per *{px['proxy_name'] if px else proxy_db_id}*\nInserisci importo (es. `52.5`):",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Annulla", callback_data=f"adm:client:{tid}")]]))
    elif d.startswith("adm:manrenew:"):
        tid = int(d.split(":")[2])
        await adm_manrenew_list(q, ctx, tid)
    elif d.startswith("adm:dorenew:"):
        parts = d.split(":")
        proxy_db_id, tid = int(parts[2]), int(parts[3])
        await adm_do_renewal(q, ctx, proxy_db_id)
    elif d.startswith("adm:setexpiry:"):
        tid = int(d.split(":")[2])
        await adm_setexpiry_list(q, ctx, tid)
    elif d.startswith("adm:setexpiry2:"):
        parts = d.split(":")
        proxy_db_id, tid = int(parts[2]), int(parts[3])
        ctx.user_data["adm"] = {"step": "setexpiry", "proxy_db_id": proxy_db_id, "tid": tid}
        ctx.user_data["adm_text"] = True
        px = get_proxy_by_id(proxy_db_id)
        await q.edit_message_text(
            f"📅 Nuova scadenza per *{px['proxy_name'] if px else proxy_db_id}* (`YYYY-MM-DD`):",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Annulla", callback_data=f"adm:client:{tid}")]]))

async def adm_clients(q):
    clients = get_all_clients()
    if not clients:
        await q.edit_message_text("Nessun cliente.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Menu", callback_data="adm:menu")]]))
        return
    kb = [[InlineKeyboardButton(client_label(cl), callback_data=f"adm:client:{cl['telegram_id']}")]
          for cl in clients]
    kb.append([InlineKeyboardButton("⬅️ Menu", callback_data="adm:menu")])
    await q.edit_message_text("👥 *Seleziona cliente:*", parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb))

async def adm_show_client(q, ctx, tid: int):
    cl = get_client(tid)
    if not cl:
        await q.edit_message_text("Cliente non trovato.")
        return
    summary = await _proxy_summary(tid)
    notes     = get_client_notes(tid)
    notes_str = f"\n📝 *Nota:* {notes}" if notes else ""
    text = f"👤 *{client_label(cl)}*\nTipo: {cl['client_type']} | 🌐 {cl['lang']}{notes_str}\n\n{summary}"
    notes = get_client_notes(tid)
    notes_label = "📝 Modifica nota" if notes else "📝 Aggiungi nota"
    kb = [
        [InlineKeyboardButton("➕ Aggiungi proxy",      callback_data=f"adm:addproxy:{tid}")],
        [InlineKeyboardButton("🗑 Rimuovi proxy",       callback_data=f"adm:removeproxy:{tid}")],
        [InlineKeyboardButton("🔄 Sposta proxy",        callback_data=f"adm:moveproxy:{tid}")],
        [InlineKeyboardButton("💶 Override prezzo",     callback_data=f"adm:overprice:{tid}")],
        [InlineKeyboardButton("✅ Rinnovo manuale",     callback_data=f"adm:manrenew:{tid}")],
        [InlineKeyboardButton("📅 Aggiorna scadenza",   callback_data=f"adm:setexpiry:{tid}")],
        [InlineKeyboardButton(notes_label,              callback_data=f"adm:editnote:{tid}")],
        [InlineKeyboardButton("🚫 Rimuovi cliente",     callback_data=f"adm:removeclient:{tid}")],
        [InlineKeyboardButton("⬅️ Clienti",            callback_data="adm:clients")],
    ]
    await q.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def _proxy_summary(tid: int) -> str:
    proxies = get_proxies(tid)
    if not proxies:
        return "_Nessun proxy attivo._"
    cl     = get_client(tid)
    is_res = cl and cl["client_type"] == "reseller"
    today  = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines  = ["*Proxy attivi:*"]
    for p in proxies:
        price = effective_price(p)
        if is_res:
            name = p["conn_id"]
            exp  = p["reseller_expiry"] or "N/D"
            icon = "🔴" if exp < today else "🟢"
            lines.append(f"• {icon} `{name}` | {p['plan_type']} | €{price:.2f} | {exp}")
        else:
            port  = str(p["port"]) if p["port"] else "?"
            login = p["proxy_login"] or ""
            label = f":{port}" + (f" ({login})" if login else "")
            lines.append(f"• 🔵 `{label}` | {p['plan_type']} | €{price:.2f} | iProxy")
    return "\n".join(lines)

async def adm_new_client_start(q, ctx):
    ctx.user_data["adm"] = {"step": "new_tid"}
    ctx.user_data["adm_text"] = True
    await q.edit_message_text(
        "➕ *Nuovo cliente*\n\nInvia il *Telegram ID* numerico\n_(chiedi all'utente di scrivere a @userinfobot)_:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Annulla", callback_data="adm:cancel")]]))

async def adm_addproxy_start(q, ctx, tid: int):
    cl = get_client(tid)
    if not cl:
        await q.edit_message_text("Cliente non trovato.")
        return
    ctx.user_data["adm"] = {"step": "conn_id", "tid": tid}
    ctx.user_data["adm_text"] = True
    await q.edit_message_text(
        f"➕ *Aggiungi proxy a {client_label(cl)}*\n\nInvia il *conn_id* (codice iProxy).\n"
        "_Il bot verificherà su iProxy e mostrerà le porte disponibili._",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Annulla", callback_data=f"adm:client:{tid}")]]))

async def adm_expiries(q):
    proxies_all = get_all_reseller_proxies()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    warn  = (datetime.now(timezone.utc) + timedelta(days=3)).strftime("%Y-%m-%d")
    lines = ["📋 *Scadenze reseller*\n"]
    for p in proxies_all:
        exp  = p["reseller_expiry"]
        name = p["tg_username"] or f"ID {p['telegram_id']}"
        pname = p["proxy_name"] or p["conn_id"]
        icon = "🔴" if exp < today else ("🟡" if exp <= warn else "🟢")
        lines.append(f"{icon} {name} | *{pname}* | {exp}")
    if len(lines) == 1:
        lines.append("Nessun reseller registrato.")
    await q.edit_message_text("\n".join(lines), parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Menu", callback_data="adm:menu")]]))

async def adm_tools(q):
    await q.edit_message_text("🔧 *Strumenti*", parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💰 Fatturato & Pagamenti",  callback_data="adm:revenue")],
            [InlineKeyboardButton("📢 Newsletter / Broadcast", callback_data="adm:broadcast")],
            [InlineKeyboardButton("⬅️ Menu",                  callback_data="adm:menu")],
        ]))

async def adm_remove_proxy_list(q, ctx, tid: int):
    proxies = get_proxies(tid)
    if not proxies:
        await q.edit_message_text("Nessun proxy.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Cliente", callback_data=f"adm:client:{tid}")]]))
        return
    kb = [[InlineKeyboardButton(f"🗑 {p['proxy_name'] or p['conn_id']}",
                                callback_data=f"adm:confirmremove:{p['id']}:{tid}")] for p in proxies]
    kb.append([InlineKeyboardButton("❌ Annulla", callback_data=f"adm:client:{tid}")])
    await q.edit_message_text("🗑 *Seleziona proxy da rimuovere:*", parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb))

async def adm_move_proxy_list(q, ctx, tid: int):
    proxies = get_proxies(tid)
    if not proxies:
        await q.edit_message_text("Nessun proxy.")
        return
    kb = [[InlineKeyboardButton(f"🔄 {p['proxy_name'] or p['conn_id']}",
                                callback_data=f"adm:movepick:{p['id']}:{tid}")] for p in proxies]
    kb.append([InlineKeyboardButton("❌ Annulla", callback_data=f"adm:client:{tid}")])
    await q.edit_message_text("🔄 *Seleziona proxy da spostare:*", parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb))

async def adm_overprice_list(q, ctx, tid: int):
    proxies = get_proxies(tid)
    if not proxies:
        await q.edit_message_text("Nessun proxy.")
        return
    kb = [[InlineKeyboardButton(f"💶 {p['proxy_name'] or p['conn_id']} (€{effective_price(p):.2f})",
                                callback_data=f"adm:overprice2:{p['id']}:{tid}")] for p in proxies]
    kb.append([InlineKeyboardButton("❌ Annulla", callback_data=f"adm:client:{tid}")])
    await q.edit_message_text("💶 *Seleziona proxy:*", parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb))

async def adm_manrenew_list(q, ctx, tid: int):
    proxies = get_proxies(tid)
    if not proxies:
        await q.edit_message_text("Nessun proxy.")
        return
    kb = [[InlineKeyboardButton(f"✅ {p['proxy_name'] or p['conn_id']}",
                                callback_data=f"adm:dorenew:{p['id']}:{tid}")] for p in proxies]
    kb.append([InlineKeyboardButton("❌ Annulla", callback_data=f"adm:client:{tid}")])
    await q.edit_message_text("✅ *Seleziona proxy da rinnovare:*", parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb))

async def adm_setexpiry_list(q, ctx, tid: int):
    cl = get_client(tid)
    if not cl or cl["client_type"] != "reseller":
        await q.edit_message_text("Solo per reseller.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️", callback_data=f"adm:client:{tid}")]]))
        return
    proxies = get_proxies(tid)
    kb = [[InlineKeyboardButton(f"📅 {p['proxy_name'] or p['conn_id']}",
                                callback_data=f"adm:setexpiry2:{p['id']}:{tid}")] for p in proxies]
    kb.append([InlineKeyboardButton("❌ Annulla", callback_data=f"adm:client:{tid}")])
    await q.edit_message_text("📅 *Seleziona proxy:*", parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb))

async def adm_do_renewal(q, ctx, proxy_db_id: int):
    px = get_proxy_by_id(proxy_db_id)
    cl = get_proxy_client(proxy_db_id)
    if not px or not cl:
        await q.edit_message_text("Proxy non trovato.")
        return
    await q.edit_message_text(f"⏳ Rinnovo manuale per *{px['proxy_name'] or px['conn_id']}*…",
        parse_mode="Markdown")
    is_res = cl["client_type"] == "reseller"
    plan_id = conn_exp = proxy_exp = None
    if not is_res:
        conn = await get_connection(px["conn_id"])
        if conn:
            pi = (conn.get("plan_info") or {}).get("active_plan") or {}
            plan_id = pi.get("id")
            conn_exp = pi.get("expires_at")
        accesses = await get_proxy_accesses(px["conn_id"])
        pa = next((a for a in accesses if a["id"] == px["proxy_id"]), None) if px["proxy_id"] else None
        proxy_exp = pa["expires_at"] if pa else None
    await do_renewal(_app.bot, proxy_db_id, effective_price(px), get_lang(cl), is_res,
                     plan_id, conn_exp, proxy_exp)
    # Auto-show client page
    await adm_show_client(q, ctx, px["telegram_id"])

async def adm_finalize_proxy(q_or_msg, ctx, tid, conn_id, proxy_id, plan, price_override, reseller_expiry):
    # Fetch proxy name and port from iProxy
    accesses = await get_proxy_accesses(conn_id)
    pa = next((a for a in accesses if a["id"] == proxy_id), None) if proxy_id else None
    conn = await get_connection(conn_id)
    conn_name = conn["basic_info"]["name"] if conn else conn_id
    if pa:
        port        = pa.get("port")
        hostname    = pa.get("hostname")
        auth        = pa.get("auth") or {}
        login       = auth.get("login", "")
        proxy_name  = f":{port}" + (f" ({login})" if login else "")
        proxy_login = login or None
    else:
        # Reseller: use connection name as proxy_name
        proxy_name  = conn_name
        port        = None
        hostname    = None
        proxy_login = None
    # Normalize plan_type — "override" is not a valid plan, default to "limited"
    plan_type = plan if plan in ("limited", "unlimited") else "limited"
    pid = add_proxy(tid, conn_id, proxy_id, proxy_name, port, hostname,
                    plan_type, price_override, reseller_expiry, proxy_login)
    ctx.user_data.pop("adm", None)
    ctx.user_data.pop("adm_text", None)
    price = price_override if price_override else DEFAULT_PRICES[plan]
    ppp   = pp_price(price)
    cl    = get_client(tid)
    summary = await _proxy_summary(tid)
    text = (f"✅ *Proxy aggiunto!*\n\n"
            f"Cliente: {client_label(cl) if cl else tid}\n"
            f"Proxy: *{proxy_name}*\n"
            f"Piano: {plan.capitalize()}\n"
            f"💶 Carta: €{price:.2f} | PayPal: €{ppp:.2f}\n"
            + (f"📅 Scadenza: {reseller_expiry}\n" if reseller_expiry else "")
            + f"\n{summary}")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("👤 Torna al cliente", callback_data=f"adm:client:{tid}")],
        [InlineKeyboardButton("⬅️ Menu",            callback_data="adm:menu")],
    ])
    if hasattr(q_or_msg, "edit_message_text"):
        await q_or_msg.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
    else:
        await q_or_msg.reply_text(text, parse_mode="Markdown", reply_markup=kb)

# ── Admin text input ───────────────────────────────────────────────────────────
async def handle_admin_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    # Check password change flow first (any user)
    if ctx.user_data.get("changepw_waiting"):
        await handle_changepw_text(update, ctx)
        return
    if not is_admin(uid) or not ctx.user_data.get("adm_text"):
        return
    adm  = ctx.user_data.get("adm", {})
    step = adm.get("step")
    text = update.message.text.strip()

    if step == "new_tid":
        try:
            tid = int(text)
        except ValueError:
            await update.message.reply_text("❌ ID numerico non valido.")
            return
        adm["tid"] = tid
        adm["step"] = "new_username"
        await update.message.reply_text("Nome/username del cliente (es. @mario o Mario Rossi):")

    elif step == "new_username":
        tid = adm["tid"]
        adm["username"] = text
        adm["step"] = "new_type"
        ctx.user_data["adm_text"] = False
        await update.message.reply_text(
            f"Tipo per *{text}* (ID `{tid}`):", parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("👤 Client",   callback_data=f"adm:newtype:{tid}:client")],
                [InlineKeyboardButton("👔 Reseller", callback_data=f"adm:newtype:{tid}:reseller")],
                [InlineKeyboardButton("❌ Annulla",  callback_data="adm:cancel")],
            ]))

    elif step == "conn_id":
        tid = adm.get("tid")
        await update.message.reply_text("⏳ Verifico la connessione su iProxy...")
        conn = await get_connection(text)
        if not conn:
            await update.message.reply_text(
                f"❌ *Connessione `{text}` non trovata su iProxy.*\nControlla il codice e riprova.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Annulla", callback_data=f"adm:client:{tid}")]]))
            return
        # Fetch proxy_accesses to let admin pick the port
        accesses = await get_proxy_accesses(text)
        if not accesses:
            await update.message.reply_text(
                f"⚠️ Connessione *{conn['basic_info']['name']}* trovata ma senza proxy access.\nCrea prima un access point su iProxy.",
                parse_mode="Markdown")
            return
        adm["conn_id"]   = text
        adm["conn_name"] = conn["basic_info"]["name"]
        client_row = get_client(tid)
        is_res_client = client_row and client_row["client_type"] == "reseller"

        if is_res_client:
            # Reseller gets the whole connection, no port picker needed
            adm["proxy_id"] = None
            adm["step"]     = "plan"
            ctx.user_data["adm_text"] = False
            conn_name = conn["basic_info"]["name"]
            await update.message.reply_text(
                f"✅ Connessione *{conn_name}* trovata.\n\nScegli il *piano*:",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(f"🔒 Limited  — €{DEFAULT_PRICES['limited']:.2f}",
                                         callback_data=f"adm:plan:{tid}:{text}:None:limited")],
                    [InlineKeyboardButton(f"🔓 Unlimited — €{DEFAULT_PRICES['unlimited']:.2f}",
                                         callback_data=f"adm:plan:{tid}:{text}:None:unlimited")],
                    [InlineKeyboardButton("💶 Override prezzo",
                                         callback_data=f"adm:plan:{tid}:{text}:None:override")],
                    [InlineKeyboardButton("❌ Annulla", callback_data=f"adm:client:{tid}")],
                ]))
        else:
            # Normal client: show port picker
            if not accesses:
                await update.message.reply_text(
                    f"⚠️ Connessione *{conn['basic_info']['name']}* trovata ma senza proxy access.\n"
                    f"Crea prima un access point su iProxy.",
                    parse_mode="Markdown")
                return
            adm["step"] = "pick_proxy"
            ctx.user_data["adm_text"] = False
            kb = []
            for pa in accesses:
                port  = pa.get("port", "?")
                proto = pa.get("listen_service", "")
                auth  = pa.get("auth") or {}
                login = auth.get("login", "")
                label = f"🔌 :{port} ({proto})"
                if login:
                    label += f" — {login}"
                kb.append([InlineKeyboardButton(label, callback_data=f"adm:pickpa:{tid}:{text}:{pa['id']}")])
            kb.append([InlineKeyboardButton("❌ Annulla", callback_data=f"adm:client:{tid}")])
            await update.message.reply_text(
                f"✅ *{conn['basic_info']['name']}* trovata.\n\nSeleziona il proxy access (porta):",
                parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

    elif step == "override_price":
        try:
            price = float(text.replace(",", "."))
        except ValueError:
            await update.message.reply_text("❌ Numero non valido.")
            return
        tid      = adm["tid"]
        conn_id  = adm["conn_id"]
        proxy_id = adm["proxy_id"]
        # plan_type for override: ask which plan it is
        adm["price_override"] = price
        adm["step"] = "override_plan_type"
        ctx.user_data["adm_text"] = False
        await update.message.reply_text(
            f"✅ Prezzo custom: €{price:.2f}\n\nOra scegli il *tipo* di piano (per categorizzazione):",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔒 Limited",   callback_data=f"adm:ovtype:{tid}:{conn_id}:{proxy_id}:limited")],
                [InlineKeyboardButton("🔓 Unlimited", callback_data=f"adm:ovtype:{tid}:{conn_id}:{proxy_id}:unlimited")],
            ]))

    elif step == "expiry":
        try:
            datetime.fromisoformat(text)
        except ValueError:
            await update.message.reply_text("❌ Formato non valido. Usa `YYYY-MM-DD` (es. 2026-07-21)", parse_mode="Markdown")
            return
        tid      = adm["tid"]
        conn_id  = adm["conn_id"]
        proxy_id = adm["proxy_id"]
        plan     = adm["plan"]
        price_ov = adm.get("price_override")
        await adm_finalize_proxy(update.message, ctx, tid, conn_id, proxy_id, plan, price_ov, text)

    elif step == "set_price":
        try:
            price = float(text.replace(",", "."))
        except ValueError:
            await update.message.reply_text("❌ Numero non valido.")
            return
        proxy_db_id = adm["proxy_db_id"]
        tid         = adm["tid"]
        set_price_override(proxy_db_id, price)
        px = get_proxy_by_id(proxy_db_id)
        tid2 = px["telegram_id"] if px else adm.get("tid")
        ctx.user_data.pop("adm", None)
        ctx.user_data.pop("adm_text", None)
        await update.message.reply_text(
            f"✅ Prezzo aggiornato: €{price:.2f}", parse_mode="Markdown")
        # Auto-show client page
        if tid2:
            cl2 = get_client(tid2)
            if cl2:
                kb2 = [[InlineKeyboardButton("👤 Torna al cliente", callback_data=f"adm:client:{tid2}")]]
                await update.message.reply_text("↩️", reply_markup=InlineKeyboardMarkup(kb2))

    elif step == "edit_note":
        tid = adm["tid"]
        note_text = "" if text.strip() == "-" else text.strip()
        set_client_notes(tid, note_text)
        ctx.user_data.pop("adm", None)
        ctx.user_data.pop("adm_text", None)
        msg = "✅ Nota cancellata." if not note_text else f"✅ Nota salvata: _{note_text}_"
        await update.message.reply_text(msg, parse_mode="Markdown")
        # Auto-show client card
        cl = get_client(tid)
        if cl:
            await update.message.reply_text(
                "↩️", reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("👤 Torna al cliente", callback_data=f"adm:client:{tid}")
                ]]))

    elif step == "setexpiry":
        try:
            datetime.fromisoformat(text)
        except ValueError:
            await update.message.reply_text("❌ Formato non valido. Usa `YYYY-MM-DD`", parse_mode="Markdown")
            return
        proxy_db_id = adm["proxy_db_id"]
        tid         = adm["tid"]
        set_reseller_expiry(proxy_db_id, text)
        px  = get_proxy_by_id(proxy_db_id)
        tid2 = px["telegram_id"] if px else adm.get("tid")
        ctx.user_data.pop("adm", None)
        ctx.user_data.pop("adm_text", None)
        await update.message.reply_text(
            f"✅ Scadenza aggiornata → {text}", parse_mode="Markdown")
        if tid2:
            kb2 = [[InlineKeyboardButton("👤 Torna al cliente", callback_data=f"adm:client:{tid2}")]]
            await update.message.reply_text("↩️", reply_markup=InlineKeyboardMarkup(kb2))

    # ── Broadcast message ─────────────────────────────────────────────────────────
    elif step == "broadcast_text":
        target = adm.get("target", "all")
        with db() as c:
            if target == "all":
                count = c.execute("SELECT COUNT(*) FROM clients").fetchone()[0]
            else:
                count = c.execute("SELECT COUNT(*) FROM clients WHERE client_type=?", (target,)).fetchone()[0]
        target_label = {"all": "tutti", "client": "Client", "reseller": "Reseller"}[target]
        _broadcast_queue[update.effective_user.id] = text
        ctx.user_data["adm_text"] = False
        await update.message.reply_text(
            f"📢 *Anteprima* — a *{count} {target_label}*:\n\n{text}\n\nConfermi?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(f"✅ Invia a {count} utenti", callback_data=f"adm:bcastconfirm:{target}")],
                [InlineKeyboardButton("✏️ Riscrivi",               callback_data=f"adm:bcast:{target}")],
                [InlineKeyboardButton("❌ Annulla",                 callback_data="adm:cancel")],
            ]))

    elif step == "move_tid":
        try:
            new_tid = int(text)
        except ValueError:
            await update.message.reply_text("❌ ID numerico non valido.")
            return
        new_cl = get_client(new_tid)
        if not new_cl:
            await update.message.reply_text(f"❌ Cliente `{new_tid}` non trovato. Registralo prima.", parse_mode="Markdown")
            return
        proxy_db_id = adm["proxy_db_id"]
        from_tid    = adm["from_tid"]
        px = get_proxy_by_id(proxy_db_id)
        move_proxy(proxy_db_id, new_tid)
        ctx.user_data.pop("adm", None)
        ctx.user_data.pop("adm_text", None)
        name2 = px["proxy_name"] if px else str(proxy_db_id)
        await update.message.reply_text(
            f"✅ *{name2}* spostato a {client_label(new_cl)}", parse_mode="Markdown")
        kb2 = [
            [InlineKeyboardButton("👤 Nuovo cliente", callback_data=f"adm:client:{new_tid}")],
            [InlineKeyboardButton("👤 Ex cliente",    callback_data=f"adm:client:{from_tid}")],
        ]
        await update.message.reply_text("↩️", reply_markup=InlineKeyboardMarkup(kb2))

# ── Callback for picking proxy_access port ─────────────────────────────────────
async def handle_pickpa_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        return
    # adm:pickpa:<tid>:<conn_id>:<proxy_id>
    parts    = q.data.split(":")
    tid      = int(parts[2])
    conn_id  = parts[3]
    proxy_id = parts[4]
    cl = get_client(tid)
    is_res = cl and cl["client_type"] == "reseller"
    ctx.user_data.setdefault("adm", {}).update({"tid": tid, "conn_id": conn_id, "proxy_id": proxy_id})
    # Ask plan — single step, no override question after
    default_l = DEFAULT_PRICES["limited"]
    default_u = DEFAULT_PRICES["unlimited"]
    await q.edit_message_text(
        f"Porta selezionata: `{proxy_id}`\n\nScegli il *piano*:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(f"🔒 Limited  — €{default_l:.2f}", callback_data=f"adm:plan:{tid}:{conn_id}:{proxy_id}:limited")],
            [InlineKeyboardButton(f"🔓 Unlimited — €{default_u:.2f}", callback_data=f"adm:plan:{tid}:{conn_id}:{proxy_id}:unlimited")],
            [InlineKeyboardButton("💶 Override prezzo",               callback_data=f"adm:plan:{tid}:{conn_id}:{proxy_id}:override")],
            [InlineKeyboardButton("❌ Annulla",                       callback_data=f"adm:client:{tid}")],
        ]))

async def handle_newtype_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        return
    parts  = q.data.split(":")  # adm:newtype:<tid>:<type>
    tid, ctype = int(parts[2]), parts[3]
    adm = ctx.user_data.get("adm", {})
    upsert_client(tid, ctype, adm.get("username"))
    ctx.user_data["adm"] = {"step": "conn_id", "tid": tid}
    ctx.user_data["adm_text"] = True
    cl = get_client(tid)
    await q.edit_message_text(
        f"✅ *{client_label(cl)}* creato.\n\nOra invia il *conn_id* della prima connessione iProxy:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Annulla", callback_data="adm:cancel")]]))

# ── Reseller reminder job ───────────────────────────────────────────────────────
async def job_monitor_connections(ctx: ContextTypes.DEFAULT_TYPE):
    """
    Runs every 5 minutes. Silent recovery:
    - If offline >= 2 min: push refresh + toggle_proxy ON silently
    - Tracks offline events in DB for daily report
    - No real-time notifications — daily report at 08:00 UTC instead
    """
    now = datetime.now(timezone.utc)
    try:
        statuses = await get_all_connections_status()
    except Exception as e:
        logger.error("job_monitor: failed to get statuses: %s", e)
        return

    tracker = {r["conn_id"]: r for r in get_offline_tracker()}

    for conn in statuses:
        conn_id = conn.get("id")
        status  = conn.get("online_status", "")
        updated = conn.get("online_updated_at")
        if not conn_id:
            continue

        if status == "online":
            if conn_id in tracker:
                clear_offline(conn_id)
                logger.info("monitor: %s back online, cleared tracker", conn_id)
            continue

        if status not in ("offline", "flapping", "nodata"):
            continue

        # How long offline?
        offline_minutes = 0
        if updated:
            try:
                offline_since = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                offline_minutes = (now - offline_since).total_seconds() / 60
            except Exception:
                pass

        if offline_minutes < 2:
            continue

        # Record first offline time (no notification)
        if conn_id not in tracker:
            record_offline(conn_id)
            logger.warning("monitor: %s offline for %.0f min — recording", conn_id, offline_minutes)

        # Rate-limit: only retry every 5 min
        trk = tracker.get(conn_id)
        if trk and trk["last_action_at"]:
            try:
                last_action = datetime.fromisoformat(trk["last_action_at"])
                if last_action.tzinfo is None:
                    last_action = last_action.replace(tzinfo=timezone.utc)
                if (now - last_action).total_seconds() / 60 < 4:
                    continue
            except Exception:
                pass

        # Silent recovery
        logger.info("monitor: silent recovery for %s", conn_id)
        try:
            await push_command(conn_id, "refresh")
            await asyncio.sleep(2)
            await push_command(conn_id, "toggle_proxy", {"enabled": True})
            record_action(conn_id)
        except Exception as e:
            logger.error("monitor: command push failed for %s: %s", conn_id, e)


async def job_daily_report(ctx: ContextTypes.DEFAULT_TYPE):
    """
    Runs every day at 08:00 UTC.
    Sends a report to @usernamehelp with:
    - Connections that were offline yesterday and recovered
    - Connections still offline now
    """
    now = datetime.now(timezone.utc)
    try:
        statuses = await get_all_connections_status()
    except Exception as e:
        logger.error("daily_report: failed to get statuses: %s", e)
        return

    tracker = {r["conn_id"]: r for r in get_offline_tracker()}
    status_map = {c["id"]: c for c in statuses if c.get("id")}

    still_offline = []
    recovered_today = []

    for conn_id, trk in tracker.items():
        current = status_map.get(conn_id, {})
        current_status = current.get("online_status", "unknown")
        actions = trk["action_count"]
        first_offline = trk["first_offline"][:16].replace("T", " ")
        if current_status == "online":
            recovered_today.append(f"  ✅ `{conn_id}` — tornato online ({actions} recovery)")
        else:
            offline_min = 0
            if trk["first_offline"]:
                try:
                    fo = datetime.fromisoformat(trk["first_offline"])
                    if fo.tzinfo is None:
                        fo = fo.replace(tzinfo=timezone.utc)
                    offline_min = (now - fo).total_seconds() / 60
                except Exception:
                    pass
            still_offline.append(
                f"  ❌ `{conn_id}` — offline da {offline_min:.0f} min ({actions} tentativi)")

    # Also flag connections that were never in tracker but are currently offline
    for conn in statuses:
        conn_id = conn.get("id")
        if conn_id and conn.get("online_status") in ("offline","flapping") and conn_id not in tracker:
            still_offline.append(f"  ⚠️ `{conn_id}` — offline (rilevato ora)")

    if not still_offline and not recovered_today:
        # All good — send brief ok
        try:
            await ctx.bot.send_message(chat_id=NOTIFY_USERNAME,
                text=f"📊 *Report giornaliero* — {now.strftime('%d/%m/%Y')}\n✅ Tutte le connessioni online. Nessun problema.",
                parse_mode="Markdown")
        except Exception:
            pass
        return

    lines = [f"📊 *Report giornaliero* — {now.strftime('%d/%m/%Y')}\n"]
    if still_offline:
        lines.append(f"❌ *Ancora offline ({len(still_offline)}):*")
        lines.extend(still_offline)
    if recovered_today:
        lines.append(f"\n✅ *Recuperate automaticamente ({len(recovered_today)}):*")
        lines.extend(recovered_today)

    try:
        await ctx.bot.send_message(chat_id=NOTIFY_USERNAME,
            text="\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        logger.error("daily_report: send failed: %s", e)

async def job_reminders(ctx: ContextTypes.DEFAULT_TYPE):
    now   = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    h48   = (now + timedelta(hours=48)).strftime("%Y-%m-%d")
    h24   = (now + timedelta(hours=24)).strftime("%Y-%m-%d")

    # ── Reseller reminders (DB expiry) ────────────────────────────────────────
    for p in get_all_reseller_proxies():
        exp  = p["reseller_expiry"]
        lang = p["lang"] if p["lang"] in SUPPORTED_LANGS else "en"
        name = p["proxy_name"] or p["conn_id"]
        tid  = p["telegram_id"]
        try:
            if exp <= h48 and exp > h24 and not p["reminder_sent_48"]:
                await ctx.bot.send_message(tid, t("reseller_reminder_48h", lang, name=name, expiry=exp),
                                           parse_mode="Markdown")
                mark_reminder(p["id"], 48)
            elif exp <= h24 and exp >= today and not p["reminder_sent_24"]:
                await ctx.bot.send_message(tid, t("reseller_reminder_24h", lang, name=name, expiry=exp),
                                           parse_mode="Markdown")
                mark_reminder(p["id"], 24)
            elif exp < today:
                await ctx.bot.send_message(NOTIFY_USERNAME,
                    f"🔴 *RESELLER NON PAGATO*\n📡 `{name}`\nTG: `{tid}`\n📅 Scaduto: {exp}\n⚠️ Disattivare su iProxy.",
                    parse_mode="Markdown")
        except Exception as e:
            logger.error("Reminder error reseller proxy %s: %s", p["id"], e)

    # ── Normal client reminders (iProxy proxy_access expiry) ──────────────────
    with db() as c:
        client_proxies = c.execute("""
            SELECT p.*, cl.lang, cl.client_type
            FROM proxies p JOIN clients cl ON cl.telegram_id=p.telegram_id
            WHERE cl.client_type='client' AND p.proxy_id IS NOT NULL
              AND p.reminder_sent_48=0
        """).fetchall()

    for p in client_proxies:
        if not p["conn_id"] or not p["proxy_id"]:
            continue
        lang = p["lang"] if p["lang"] in SUPPORTED_LANGS else "en"
        name = proxy_display_name(p, False)
        tid  = p["telegram_id"]
        try:
            accesses = await get_proxy_accesses(p["conn_id"])
            pa = next((a for a in accesses if a.get("id") == p["proxy_id"]), None)
            if not pa:
                continue
            expires_at = pa.get("expires_at")
            if not expires_at:
                continue
            exp_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            hours_left = (exp_dt - now).total_seconds() / 3600
            if 0 < hours_left <= 48:
                expiry_str = format_expiry(expires_at)
                await ctx.bot.send_message(
                    tid, t("client_reminder_48h", lang, name=name, expiry=expiry_str),
                    parse_mode="Markdown")
                mark_reminder(p["id"], 48)
                logger.info("48h reminder sent to client: proxy=%s tid=%s", p["id"], tid)
        except Exception as e:
            logger.error("Reminder error client proxy %s: %s", p["id"], e)

# ── Broadcast ─────────────────────────────────────────────────────────────────

async def adm_revenue(q):
    now        = datetime.now(timezone.utc)
    month_this = now.strftime("%B %Y")
    month_last = ((now.replace(day=1) - timedelta(days=1)).replace(day=1)).strftime("%B %Y")
    this_rows  = get_payments_this_month()
    last_rows  = get_payments_last_month()
    this_rev   = get_revenue(this_rows)
    last_rev   = get_revenue(last_rows)
    by_prov: dict[str, float] = {}
    for r in this_rows:
        by_prov[r["provider"]] = by_prov.get(r["provider"], 0) + r["amount"]
    prov_lines = "\n".join(f"  • {p}: €{a:.2f}" for p, a in sorted(by_prov.items())) or "  _nessuno_"
    recent = this_rows[:10]
    rec_lines = "\n".join(
        f"  {r['paid_at'][:10]} | {r['proxy_name'] or '?'} | €{r['amount']:.2f} ({r['provider']})"
        for r in recent
    ) or "  _nessuno_"
    text = (
        f"💰 *Fatturato*\n\n"
        f"📅 *{month_this}:* €{this_rev:.2f} ({len(this_rows)} rinnovi)\n"
        f"📅 *{month_last}:* €{last_rev:.2f} ({len(last_rows)} rinnovi)\n\n"
        f"*Per metodo ({month_this}):*\n{prov_lines}\n\n"
        f"*Ultimi 10 pagamenti:*\n{rec_lines}"
    )
    await q.edit_message_text(text, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Strumenti", callback_data="adm:tools")]]))

_broadcast_queue: dict[int, str] = {}

async def _do_broadcast(bot, q, target: str):
    admin_tid = q.from_user.id
    text = _broadcast_queue.pop(admin_tid, None)
    if not text:
        await q.edit_message_text("❌ Messaggio non trovato. Riprova da /admin → Strumenti.")
        return
    with db() as c:
        if target == "all":
            rows = c.execute("SELECT telegram_id FROM clients").fetchall()
        else:
            rows = c.execute("SELECT telegram_id FROM clients WHERE client_type=?", (target,)).fetchall()
    sent = failed = 0
    for row in rows:
        try:
            await bot.send_message(chat_id=row["telegram_id"], text=text, parse_mode="Markdown")
            sent += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            logger.warning("Broadcast failed for %s: %s", row["telegram_id"], e)
            failed += 1
    await q.edit_message_text(
        f"✅ *Broadcast completato!*\n\n📨 Inviati: *{sent}*\n❌ Falliti: *{failed}*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Menu", callback_data="adm:menu")]]))
    logger.info("Broadcast done: target=%s sent=%d failed=%d", target, sent, failed)

# ── Flask webhook ──────────────────────────────────────────────────────────────
flask_app = Flask(__name__)
_app: Application | None = None

@flask_app.route("/stripe-webhook", methods=["POST"])
def stripe_webhook():
    try:
        event = stripe.Webhook.construct_event(
            flask_request.data, flask_request.headers.get("Stripe-Signature",""), STRIPE_WEBHOOK_SECRET)
    except stripe.error.SignatureVerificationError:
        return jsonify({"error": "bad sig"}), 400
    if event["type"] == "checkout.session.completed":
        meta = event["data"]["object"].get("metadata", {})
        fire_renewal(meta, "stripe")
    return jsonify({"status": "ok"})

@flask_app.route("/paypal-success")
def paypal_success():
    order_id = flask_request.args.get("token")
    if order_id:
        threading.Thread(target=lambda: asyncio.run(_capture_paypal(order_id)), daemon=True).start()
    return "<h2>✅ Payment received! Return to Telegram.</h2>"

async def _capture_paypal(order_id: str):
    cap = await paypal_capture(order_id)
    if not cap or cap.get("status") != "COMPLETED":
        logger.error("PayPal capture failed: %s", order_id)
        return
    try:
        meta = json.loads(cap["purchase_units"][0].get("custom_id", "{}"))
    except Exception:
        return
    fire_renewal(meta, "paypal")

@flask_app.route("/coingate-webhook", methods=["POST"])
def coingate_webhook():
    data = flask_request.form
    status = data.get("status")
    if status == "paid":
        try:
            meta = json.loads(data.get("token", "{}"))
        except Exception:
            return jsonify({"error": "bad token"}), 400
        fire_renewal(meta, "coingate")
    return jsonify({"status": "ok"})

@flask_app.route("/payment-success")
def pay_ok():
    return "<h2>✅ Payment received! Return to Telegram for confirmation.</h2>"

@flask_app.route("/payment-cancel")
def pay_cancel():
    return "<h2>❌ Payment cancelled. Return to Telegram to try again.</h2>"

@flask_app.route("/paypal-cancel")
def paypal_cancel():
    return "<h2>❌ PayPal payment cancelled.</h2>"

# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    global _app
    db_init()

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    _app = app

    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("language", cmd_language))
    app.add_handler(CommandHandler("history",  cmd_history))
    app.add_handler(CommandHandler("admin",    cmd_admin))
    app.add_handler(CommandHandler("revenue",  cmd_revenue))

    # Specific patterns first
    app.add_handler(CallbackQueryHandler(handle_lang,          pattern=r"^lang:"))
    app.add_handler(CallbackQueryHandler(handle_status_lang,   pattern=r"^status_lang:"))
    app.add_handler(CallbackQueryHandler(handle_slang,         pattern=r"^slang:"))
    app.add_handler(CallbackQueryHandler(handle_selproxy,      pattern=r"^selproxy:"))
    app.add_handler(CallbackQueryHandler(handle_pay,           pattern=r"^pay:(card|paypal|crypto)"))
    app.add_handler(CallbackQueryHandler(handle_manage,        pattern=r"^manage:"))
    app.add_handler(CallbackQueryHandler(handle_proxymenu,     pattern=r"^proxymenu:"))
    app.add_handler(CallbackQueryHandler(handle_proxyrenew,    pattern=r"^proxyrenew:"))
    app.add_handler(CallbackQueryHandler(handle_renewall,      pattern=r"^renewall:"))
    app.add_handler(CallbackQueryHandler(handle_payall,        pattern=r"^payall:"))
    app.add_handler(CallbackQueryHandler(handle_back_selector, pattern=r"^back_selector:"))
    app.add_handler(CallbackQueryHandler(handle_back_status,   pattern=r"^back_status:"))
    app.add_handler(CallbackQueryHandler(handle_changepw,      pattern=r"^changepw:"))
    app.add_handler(CallbackQueryHandler(handle_changepw_cancel, pattern=r"^changepw_cancel:"))
    app.add_handler(CallbackQueryHandler(handle_newtype_cb,    pattern=r"^adm:newtype:"))
    app.add_handler(CallbackQueryHandler(handle_pickpa_cb,     pattern=r"^adm:pickpa:"))
    app.add_handler(CallbackQueryHandler(handle_admin_cb,      pattern=r"^adm:"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_text))

    app.job_queue.run_repeating(job_reminders,            interval=3600, first=60)
    app.job_queue.run_repeating(job_monitor_connections,  interval=120,  first=60)
    # Daily report at 08:00 UTC
    app.job_queue.run_daily(job_daily_report, time=datetime.strptime("08:00", "%H:%M").replace(tzinfo=timezone.utc).timetz())

    threading.Thread(target=lambda: flask_app.run(host="0.0.0.0", port=8080), daemon=True).start()
    logger.info("Bot v5 avviato")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
