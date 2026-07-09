# ============================================================
#   smsotps.com Telegram Bot  |  English Version
#   API: https://api.smsotps.com/api
# ============================================================

import telebot
from telebot import types
import requests
import json
import os
import sys
import threading
import time
from datetime import datetime

# Windows UTF-8 fix
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ─────────────── Config ───────────────
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
USERS_FILE  = os.path.join(BASE_DIR, "users.json")

with open(CONFIG_FILE, "r", encoding="utf-8") as f:
    CONFIG = json.load(f)

BOT_TOKEN        = CONFIG["bot_token"]
ADMIN_ID         = int(CONFIG["admin_id"])
FORWARD_GROUP_ID = CONFIG.get("forward_group_id")  # None means disabled
API_ID           = CONFIG.get("api_id")
API_HASH         = CONFIG.get("api_hash")

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
import asyncio

_loop = asyncio.new_event_loop()

def _run_event_loop(loop):
    asyncio.set_event_loop(loop)
    loop.run_forever()

_loop_thread = threading.Thread(target=_run_event_loop, args=(_loop,), daemon=True)
_loop_thread.start()

_checker_setup = {}

def filter_checker_report(report_text: str, phone_numbers: list) -> str:
    if not report_text or not phone_numbers:
        return report_text
    lines = report_text.strip().split('\n')
    filtered_lines = []
    for line in lines:
        for num in phone_numbers:
            clean_num = "".join(c for c in num if c.isdigit())
            clean_line = "".join(c for c in line if c.isdigit())
            if clean_num and clean_line and (clean_num in clean_line or clean_line in clean_num):
                filtered_lines.append(line)
                break
    return "\n".join(filtered_lines)

def run_async(coro):
    if coro is None:
        return None
    future = asyncio.run_coroutine_threadsafe(coro, _loop)
    return future.result()

def check_number_via_bot(number: str) -> str:
    session_path = os.path.join(BASE_DIR, 'checker_session.session')
    if not os.path.exists(session_path):
        return ""
    
    async def _check():
        client = TelegramClient(os.path.join(BASE_DIR, 'checker_session'), API_ID, API_HASH, loop=_loop)
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            return "❌ Checker account not authorized. Please setup checker again."
        
        try:
            async with client.conversation("@DustOtpBot", timeout=25) as conv:
                num_to_send = number if number.startswith('+') else f"+{number}"
                await conv.send_message(num_to_send)
                await asyncio.sleep(12)
                messages = await client.get_messages("@DustOtpBot", limit=1)
                text = messages[0].text if messages else ""
                await client.disconnect()
                return filter_checker_report(text, [number])
        except Exception as e:
            await client.disconnect()
            return f"Error checking: {str(e)}"
            
    try:
        return run_async(_check())
    except Exception as e:
        return f"Error running check: {str(e)}"

def check_multiple_numbers_via_bot(numbers: list) -> str:
    session_path = os.path.join(BASE_DIR, 'checker_session.session')
    if not os.path.exists(session_path):
        return ""
    if not numbers:
        return ""
    
    async def _check():
        client = TelegramClient(os.path.join(BASE_DIR, 'checker_session'), API_ID, API_HASH, loop=_loop)
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            return "❌ Checker account not authorized. Please setup checker again."
        
        try:
            async with client.conversation("@DustOtpBot", timeout=35) as conv:
                formatted = []
                for n in numbers:
                    num_to_send = n if n.startswith('+') else f"+{n}"
                    formatted.append(num_to_send)
                msg_text = "\n".join(formatted)
                
                await conv.send_message(msg_text)
                await asyncio.sleep(15)
                messages = await client.get_messages("@DustOtpBot", limit=1)
                text = messages[0].text if messages else ""
                await client.disconnect()
                return filter_checker_report(text, numbers)
        except Exception as e:
            await client.disconnect()
            return f"Error checking: {str(e)}"
            
    try:
        return run_async(_check())
    except Exception as e:
        return f"Error running check: {str(e)}"

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# ─────────────── API Constants ───────────────
API_BASE = "https://api.smsotps.com/api"
CDN_BASE = "https://smsotps.com"

PROVIDERS = {
    "A": "provider_a",
    "B": "provider_b",
    "D": "provider_d",
}

# ─────────────── User Data ───────────────
def load_users() -> dict:
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_users(data: dict):
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_user(uid: int) -> dict:
    return load_users().get(str(uid), {})

def set_user(uid: int, data: dict):
    users = load_users()
    users[str(uid)] = data
    save_users(users)

def update_user(uid: int, **kwargs):
    u = get_user(uid)
    u.update(kwargs)
    set_user(uid, u)

# ─────────────── Cache ───────────────
_cache = {}

# ─────────────── Cancel Flags (bulk order thread stop) ───────────────
_cancel_flags: set = set()  # group_ids that have been cancelled

# ─────────────── Session Message Tracker ───────────────
_session_msg: dict      = {}   # uid -> message_id of the active editable message
_order_msg: dict        = {}   # uid -> {order_id: message_id}
_upsend_locks: dict     = {}   # uid -> threading.Lock()
_upsend_meta            = threading.Lock()

def _get_ulock(uid: int) -> threading.Lock:
    """Return (or create) a per-user lock so upsend() is race-condition free."""
    with _upsend_meta:
        if uid not in _upsend_locks:
            _upsend_locks[uid] = threading.Lock()
        return _upsend_locks[uid]

def upsend(uid: int, text: str, reply_markup=None):
    """Always keeps exactly ONE session message in chat.
    Strategy: try edit first (in-place, no flash), fall back to delete+send on any error.
    Thread-safe + persistent across restarts."""
    with _get_ulock(uid):
        mid = _session_msg.get(uid)
        if not mid:
            # Recover from persistent storage after restart
            mid = get_user(uid).get("session_msg_id")
            if mid:
                _session_msg[uid] = mid

        if mid:
            try:
                bot.edit_message_text(text, uid, mid,
                                      reply_markup=reply_markup,
                                      parse_mode="HTML")
                return   # ✅ edit succeeded
            except Exception as e:
                err = str(e).lower()
                if "message is not modified" in err or "not modified" in err:
                    return   # same content — already showing correct message

                # Edit failed for any other reason (deleted, too old, etc.)
                # Delete the stale message so chat stays clean, then send fresh
                try:
                    bot.delete_message(uid, mid)
                except Exception:
                    pass
                _session_msg.pop(uid, None)
                update_user(uid, session_msg_id=None)

        m = bot.send_message(uid, text, reply_markup=reply_markup, parse_mode="HTML")
        _session_msg[uid] = m.message_id
        update_user(uid, session_msg_id=m.message_id)  # persist for next restart

def track_order_msg(uid: int, order_id: str, message_id: int):
    """Remember which message_id belongs to a given order, so we can edit it later."""
    if uid not in _order_msg:
        _order_msg[uid] = {}
    _order_msg[uid][order_id] = message_id

def get_order_msg_id(uid: int, order_id: str) -> int | None:
    """Return the tracked message_id for this order, or None."""
    return _order_msg.get(uid, {}).get(order_id)

def _del(uid: int, msg_id: int):
    """Silently delete a message — used to clean up reply keyboard presses in private chat."""
    try:
        bot.delete_message(uid, msg_id)
    except Exception:
        pass

def cached_get(url: str, ttl: int = 3600):
    now = time.time()
    if url in _cache and now - _cache[url]["ts"] < ttl:
        return _cache[url]["data"]
    try:
        r = requests.get(url, headers={"Accept": "application/json"}, timeout=10)
        if r.ok:
            _cache[url] = {"data": r.json(), "ts": now}
            return _cache[url]["data"]
    except Exception:
        pass
    return None

# ─────────────── smsotps API ───────────────
class SMSOtpsAPI:
    def __init__(self, api_key: str):
        self.key = api_key
        self.headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-API-KEY": api_key,
        }

    def _get(self, path: str):
        try:
            r = requests.get(f"{API_BASE}{path}", headers=self.headers, timeout=10)
            return r.json()
        except Exception as e:
            return {"error": str(e)}

    def _post(self, path: str, body: dict = None):
        try:
            r = requests.post(f"{API_BASE}{path}", headers=self.headers, json=body or {}, timeout=10)
            return r.json()
        except Exception as e:
            return {"error": str(e)}

    def balance(self):
        return self._get("/balance")

    def order_number(self, provider, service, country, max_price=None, operator=None):
        body = {"provider": provider, "service": service, "country": country}
        if max_price:
            body["max_price"] = max_price
        if operator and operator != "any":
            body["operator"] = operator
        return self._post("/order-number", body)

    def order_number_with_fallback(self, provider, service, country, allowed_prices=None, max_price=None, operator=None):
        # 1. Fetch balance
        bal_data = self.balance()
        bal = 0.0
        try:
            bal = float(bal_data.get("balance", 0))
        except Exception:
            pass

        # 2. Fetch offers
        offers_data = self.offers(provider, service, country)

        price_to_count = {}
        if isinstance(offers_data, list):
            for op in offers_data:
                op_name = op.get("name")
                if operator and operator != "any" and op_name != operator:
                    continue
                offers = op.get("offers", [])
                if isinstance(offers, list):
                    for o in offers:
                        pr = o.get("price")
                        cnt = o.get("count", 0)
                        if pr:
                            try:
                                p_float = float(pr)
                                price_to_count[p_float] = max(price_to_count.get(p_float, 0), int(cnt))
                            except ValueError:
                                pass
        available_offers = [(pr, cnt) for pr, cnt in price_to_count.items()]

        # Select target prices
        target_prices = []
        if allowed_prices:
            target_prices = sorted([float(x) for x in allowed_prices])
        elif max_price:
            target_prices = sorted([pr for pr, cnt in available_offers if pr <= float(max_price)])
        else:
            target_prices = sorted([pr for pr, cnt in available_offers if cnt > 0])

        if not target_prices and available_offers:
            target_prices = sorted([pr for pr, cnt in available_offers])

        if not target_prices:
            return self.order_number(provider, service, country, max_price=max_price, operator=operator)

        last_res = {"error": "No numbers available"}
        for pr in target_prices:
            if pr > bal:
                last_res = {"error": f"Insufficient balance (Cheapest number is ${pr:.4f}, your balance is ${bal:.4f})"}
                break

            res = self.order_number(provider, service, country, max_price=pr, operator=operator)
            if res and "error" not in res and ("number" in res or "phone" in res):
                return res
            last_res = res

            err_str = str(res.get("error", "")).lower()
            if "balance" in err_str or "money" in err_str or "insufficient" in err_str:
                break

        return last_res

    def number_status(self, order_id: int):
        return self._get(f"/number-status/{order_id}")

    def cancel_number(self, order_id: int):
        return self._post(f"/cancel-number/{order_id}")

    def resend_sms(self, order_id: int):
        return self._post(f"/resend-sms/{order_id}")

    def offers(self, provider_letter: str, service: str, country: int):
        return self._get(f"/p_{provider_letter.lower()}/offers/{service}/{country}")

    def validate_key(self) -> bool:
        return "balance" in self.balance()

# ─────────────── Data Loaders ───────────────
def get_countries(provider_letter: str) -> dict:
    return cached_get(f"{CDN_BASE}/provider_{provider_letter.lower()}_countries.json") or {}

def get_services(provider_letter: str) -> dict:
    return cached_get(f"{CDN_BASE}/provider_{provider_letter.lower()}_services.json") or {}

# ─────────────── Helpers ───────────────
def strip_country_code(number: str) -> str:
    """Remove international country code prefix, return local subscriber number."""
    num = number.lstrip('+')
    # Try longest codes first to avoid false matches
    codes_3 = [
        '370','371','372','373','374','375','376','377','378','379',
        '380','381','382','383','385','386','387','389',
        '420','421','423',
        '500','501','502','503','504','505','506','507','508','509',
        '590','591','592','593','594','595','596','597','598','599',
        '670','672','673','674','675','676','677','678','679',
        '680','681','682','683','685','686','687','688','689',
        '690','691','692',
        '850','852','853','855','856',
        '880','886',
        '960','961','962','963','964','965','966','967','968',
        '970','971','972','973','974','975','976','977',
        '992','993','994','995','996','998',
    ]
    codes_2 = [
        '20','27','30','31','32','33','34','36','39',
        '40','41','43','44','45','46','47','48','49',
        '51','52','53','54','55','56','57','58',
        '60','61','62','63','64','65','66',
        '81','82','84','86',
        '90','91','92','93','94','95','98',
    ]
    codes_1 = ['1', '7']
    for code in codes_3:
        if num.startswith(code):
            return num[3:]
    for code in codes_2:
        if num.startswith(code):
            return num[2:]
    for code in codes_1:
        if num.startswith(code):
            return num[1:]
    return num

def flag(country_name: str) -> str:
    flags = {
        "USA": "🇺🇸", "United Kingdom": "🇬🇧", "Russia": "🇷🇺",
        "Ukraine": "🇺🇦", "Germany": "🇩🇪", "France": "🇫🇷",
        "India": "🇮🇳", "China": "🇨🇳", "Bangladesh": "🇧🇩",
        "Pakistan": "🇵🇰", "Indonesia": "🇮🇩", "Philippines": "🇵🇭",
        "Turkey": "🇹🇷", "Brazil": "🇧🇷", "Canada": "🇨🇦",
        "Australia": "🇦🇺", "Japan": "🇯🇵", "South Korea": "🇰🇷",
        "Vietnam": "🇻🇳", "Thailand": "🇹🇭", "Malaysia": "🇲🇾",
        "Singapore": "🇸🇬", "Mexico": "🇲🇽", "Spain": "🇪🇸",
        "Italy": "🇮🇹", "Poland": "🇵🇱", "Netherlands": "🇳🇱",
        "Belgium": "🇧🇪", "Sweden": "🇸🇪", "Norway": "🇳🇴",
        "Saudi Arabia": "🇸🇦", "UAE": "🇦🇪", "Egypt": "🇪🇬",
        "Nigeria": "🇳🇬", "South Africa": "🇿🇦", "Kenya": "🇰🇪",
        "Argentina": "🇦🇷", "Colombia": "🇨🇴", "Chile": "🇨🇱",
        "Romania": "🇷🇴", "Kazakhstan": "🇰🇿", "Uzbekistan": "🇺🇿",
        "Myanmar": "🇲🇲", "Cambodia": "🇰🇭", "Iran": "🇮🇷",
        "Iraq": "🇮🇶", "Israel": "🇮🇱", "Portugal": "🇵🇹",
        "Greece": "🇬🇷", "Czech": "🇨🇿", "Hungary": "🇭🇺",
        "Hong Kong": "🇭🇰", "Taiwan": "🇹🇼", "Morocco": "🇲🇦",
    }
    return flags.get(country_name, "🌐")

def service_emoji(name: str) -> str:
    emojis = {
        "Telegram": "✈️", "Whatsapp": "💬", "facebook": "📘",
        "Instagram": "📸", "Google": "🔍", "Twitter": "🐦",
        "TikTok": "🎵", "Discord": "🎮", "Snapchat": "👻",
        "Amazon": "📦", "Uber": "🚗", "Apple": "🍎",
        "Microsoft": "🪟", "Netflix": "🎬", "Steam": "🎮",
        "OpenAI": "🤖", "Tinder": "❤️", "LinkedIn": "💼",
    }
    for k, v in emojis.items():
        if k.lower() in name.lower():
            return v
    return "📱"

def is_logged_in(uid: int) -> bool:
    return bool(get_user(uid).get("api_key"))

def get_api(uid: int):
    u = get_user(uid)
    return SMSOtpsAPI(u["api_key"]) if u.get("api_key") else None

# ─────────────── Favorites ───────────────
def get_fav_countries(uid: int, prov_letter: str) -> list:
    """Return list of country IDs (str) favorited by this user for a given provider."""
    return get_user(uid).get("fav_countries", {}).get(prov_letter, [])

def add_fav_country(uid: int, prov_letter: str, cid: str):
    u    = get_user(uid)
    favs = u.get("fav_countries", {})
    lst  = favs.get(prov_letter, [])
    if str(cid) not in lst:
        lst = [str(cid)] + lst   # newest at top
    favs[prov_letter] = lst
    update_user(uid, fav_countries=favs)

def remove_fav_country(uid: int, prov_letter: str, cid: str):
    u    = get_user(uid)
    favs = u.get("fav_countries", {})
    favs[prov_letter] = [c for c in favs.get(prov_letter, []) if c != str(cid)]
    update_user(uid, fav_countries=favs)

# ─────────────── OTP Group Forwarder ───────────────
def _forward_otp_to_group(uid: int, order_id: str, number: str,
                          otp_code: str, service: str, full_text: str = ""):
    """Send OTP notification to the configured forward group."""
    global FORWARD_GROUP_ID
    if not FORWARD_GROUP_ID:
        return
    try:
        # Resolve service code → full name (e.g. "tg" → "Telegram")
        sname = service
        for prov in ("A", "B", "D"):
            svcs = get_services(prov)
            if service in svcs:
                sname = svcs[service]
                break

        text = (
            f"📞 Number: {number}\n"
            f"🔑 OTP: <code>{otp_code}</code>\n"
            f"📱 Service: {sname}\n"
            f"🆔 Order: #{order_id}\n"
            f"👤 User: {uid}\n"
            + (f"📩 SMS: {full_text}\n" if full_text else "")
            + f"🕒 Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        bot.send_message(FORWARD_GROUP_ID, text)
    except Exception as e:
        print(f"[!] OTP forward failed: {e}")

# ─────────────── Keyboards ───────────────
def main_menu_keyboard(uid: int) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("💰 Balance",       callback_data="balance"),
        types.InlineKeyboardButton("📱 Buy Number",    callback_data="buy_menu"),
    )
    kb.add(
        types.InlineKeyboardButton("📋 My Orders",     callback_data="my_orders"),
        types.InlineKeyboardButton("📜 History",       callback_data="history"),
    )
    kb.add(
        types.InlineKeyboardButton("🔑 Change API Key", callback_data="change_key"),
        types.InlineKeyboardButton("❌ Logout",         callback_data="logout"),
    )
    if uid == ADMIN_ID:
        kb.add(types.InlineKeyboardButton("👑 Admin Panel", callback_data="admin"))
    return kb

def back_keyboard(dest: str = "main") -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("⬅️ Back", callback_data=f"back_{dest}"))
    return kb

def provider_keyboard() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=3)
    kb.add(
        types.InlineKeyboardButton("🅰️ Provider A", callback_data="prov_A"),
        types.InlineKeyboardButton("🅱️ Provider B", callback_data="prov_B"),
        types.InlineKeyboardButton("🅳 Provider D", callback_data="prov_D"),
    )
    kb.add(types.InlineKeyboardButton("⬅️ Back", callback_data="back_main"))
    return kb



def reply_keyboard(uid: int = 0) -> types.ReplyKeyboardMarkup:
    """Persistent bottom keyboard — mirrors the Main Menu inline buttons."""
    kb = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    kb.add(
        types.KeyboardButton("💰 Balance"),
        types.KeyboardButton("📱 Buy Number"),
    )
    kb.add(
        types.KeyboardButton("📋 My Orders"),
        types.KeyboardButton("📜 History"),
    )
    kb.add(
        types.KeyboardButton("🔑 Change API Key"),
        types.KeyboardButton("❌ Logout"),
    )
    kb.add(
        types.KeyboardButton("⚙️ Setup Checker"),
        types.KeyboardButton("🗑️ Remove Checker"),
    )
    return kb

# ─────────────── Register Bot Commands (Left Menu) ───────────────
def register_commands():
    commands = [
        types.BotCommand("start",      "▶️ Start / Main Menu"),
        types.BotCommand("menu",       "🏠 Show Menu"),
        types.BotCommand("balance",    "💰 Check Balance"),
        types.BotCommand("buy",        "📱 Buy a Number"),
        types.BotCommand("orders",     "📋 My Active Orders"),
        types.BotCommand("history",    "📜 Order History"),
        types.BotCommand("logout",     "❌ Logout"),
        types.BotCommand("setgroup",   "📨 Set OTP Forward Group (Admin)"),
        types.BotCommand("unsetgroup", "❌ Remove OTP Forward Group (Admin)"),
    ]
    try:
        bot.set_my_commands(commands)
        print("[*] Bot commands registered successfully.")
    except Exception as e:
        print(f"[!] Failed to register commands: {e}")

# ─────────────── /start ───────────────
@bot.message_handler(commands=["start"])
def cmd_start(msg: types.Message):
    uid = msg.from_user.id
    if is_logged_in(uid):
        bot.send_message(
            uid,
            f"👋 <b>Welcome back!</b>\n\nUse the menu below to get started 👇",
            reply_markup=reply_keyboard(uid),
        )
    else:
        update_user(uid, state="awaiting_key")
        bot.send_message(
            uid,
            "👋 <b>Welcome to smsotps Bot!</b>\n\n"
            "📌 Please send your <b>smsotps API Key</b> to login.\n\n"
            "🔗 Get your API Key at: <a href='https://smsotps.com/profile'>smsotps.com/profile</a>",
        )

@bot.message_handler(commands=["menu"])
def cmd_menu(msg: types.Message):
    uid = msg.from_user.id
    if not is_logged_in(uid):
        bot.send_message(uid, "⚠️ Please login first with /start")
        return
    bot.send_message(uid, "🏠 <b>Main Menu</b>", reply_markup=main_menu_keyboard(uid),
                     )

@bot.message_handler(commands=["balance"])
def cmd_balance(msg: types.Message):
    uid = msg.from_user.id
    if not is_logged_in(uid):
        bot.send_message(uid, "⚠️ Please login first with /start")
        return
    api = get_api(uid)
    result = api.balance()
    if "balance" in result:
        bot.send_message(uid, f"💰 <b>Balance:</b> ${result['balance']} {result.get('currency','USD')}")
    else:
        bot.send_message(uid, "❌ Failed to fetch balance.")

@bot.message_handler(commands=["buy"])
def cmd_buy(msg: types.Message):
    uid = msg.from_user.id
    if not is_logged_in(uid):
        bot.send_message(uid, "⚠️ Please login first with /start")
        return
    bot.send_message(uid, "📱 <b>Buy Number</b>\n\nSelect a Provider:", reply_markup=provider_keyboard())

@bot.message_handler(commands=["orders"])
def cmd_orders(msg: types.Message):
    uid = msg.from_user.id
    if not is_logged_in(uid):
        bot.send_message(uid, "⚠️ Please login first with /start")
        return
    _show_my_orders_msg(uid)

@bot.message_handler(commands=["history"])
def cmd_history(msg: types.Message):
    uid = msg.from_user.id
    if not is_logged_in(uid):
        bot.send_message(uid, "⚠️ Please login first with /start")
        return
    _show_history_msg(uid)

@bot.message_handler(commands=["logout"])
def cmd_logout(msg: types.Message):
    uid = msg.from_user.id
    set_user(uid, {})
    bot.send_message(uid, "👋 You have been logged out. Use /start to login again.")

@bot.message_handler(commands=["setgroup"])
def cmd_setgroup(msg: types.Message):
    """Admin-only: send this in a group to set it as the OTP forward group."""
    global FORWARD_GROUP_ID
    uid = msg.from_user.id
    if uid != ADMIN_ID:
        return  # silently ignore non-admins
    if msg.chat.type in ("group", "supergroup"):
        FORWARD_GROUP_ID = msg.chat.id
        CONFIG["forward_group_id"] = FORWARD_GROUP_ID
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(CONFIG, f, ensure_ascii=False, indent=2)
        bot.send_message(
            msg.chat.id,
            f"✅ <b>OTP Forward Group সেট হয়েছে!</b>\n\n"
            f"🔗 Group ID: <code>{msg.chat.id}</code>\n"
            f"🏠 Group: <b>{msg.chat.title}</b>\n\n"
            f"📨 এখন থেকে সব OTP এই গ্রুপে forward হবে।",
        )
    else:
        bot.send_message(
            uid,
            "⚠️ এই command টি একটি গ্রুপ-এ send করুন!\n\n"
            "📝 Steps:\n"
            "1. Bot-কে আপনার group-এ add করুন\n"
            "2. সেই group-এ /setgroup লিখুন",
        )

@bot.message_handler(commands=["unsetgroup"])
def cmd_unsetgroup(msg: types.Message):
    """Admin-only: disable OTP forwarding."""
    global FORWARD_GROUP_ID
    uid = msg.from_user.id
    if uid != ADMIN_ID:
        return
    FORWARD_GROUP_ID = None
    CONFIG["forward_group_id"] = None
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(CONFIG, f, ensure_ascii=False, indent=2)
    bot.send_message(uid, "❌ OTP forwarding বন্ধ হয়েছে।")

# ─────────────── Message Handler ───────────────
@bot.message_handler(func=lambda m: m.chat.type == "private")
def handle_text(msg: types.Message):
    if not msg.text:   # ignore stickers, photos, voice etc.
        return
    uid  = msg.from_user.id
    text = msg.text.strip()
    u    = get_user(uid)
    state = u.get("state", "")

    # API Key
    if state == "awaiting_key":
        api = SMSOtpsAPI(text)
        bot.send_message(uid, "⏳ Validating API Key...")
        if api.validate_key():
            update_user(uid, api_key=text, state="", orders={})
            bal = api.balance()
            bot.send_message(
                uid,
                f"✅ <b>Login Successful!</b>\n\n"
                f"💰 Balance: <b>${bal.get('balance','?')} {bal.get('currency','USD')}</b>\n\n"
                f"Use the menu below 👇",
                reply_markup=reply_keyboard(uid),
            )
            m = bot.send_message(
                uid,
                "🏠 <b>Main Menu</b>",
                reply_markup=main_menu_keyboard(uid),
            )
            _session_msg[uid] = m.message_id  # track the main menu message
        else:
            bot.send_message(
                uid,
                "❌ <b>Invalid API Key!</b>\n\n"
                "Please try again or get your key from "
                "<a href='https://smsotps.com/profile'>smsotps.com/profile</a>",
            )
        return

    # Setup Checker States
    if state == "setup_checker_phone":
        phone = text.strip()
        bot.send_message(uid, f"⏳ Connecting and sending code to {phone}...")
        try:
            client = TelegramClient(os.path.join(BASE_DIR, 'checker_session'), API_ID, API_HASH, loop=_loop)
            run_async(client.connect())
            
            sent_code = run_async(client.send_code_request(phone))
            
            _checker_setup[uid] = {
                "client": client,
                "phone": phone,
                "phone_code_hash": sent_code.phone_code_hash
            }
            update_user(uid, state="setup_checker_code")
            bot.send_message(
                uid,
                f"✅ Code sent to {phone}.\n\n"
                "Please enter the login code you received from Telegram (e.g. <code>12345</code>):"
            )
        except Exception as e:
            bot.send_message(uid, f"❌ Error sending code: <code>{str(e)}</code>")
            update_user(uid, state="")
        return

    if state == "setup_checker_code":
        code = text.strip()
        setup_data = _checker_setup.get(uid)
        if not setup_data:
            bot.send_message(uid, "❌ Session expired or not found. Please start over.")
            update_user(uid, state="")
            return
        
        client = setup_data["client"]
        phone = setup_data["phone"]
        phone_code_hash = setup_data["phone_code_hash"]
        
        bot.send_message(uid, "⏳ Verifying code...")
        try:
            run_async(client.sign_in(phone, code, phone_code_hash=phone_code_hash))
            bot.send_message(uid, "🎉 <b>Checker Setup Successful!</b>\n\nTelethon session has been saved and activated.")
            update_user(uid, state="")
            _checker_setup.pop(uid, None)
            run_async(client.disconnect())
        except SessionPasswordNeededError:
            update_user(uid, state="setup_checker_password")
            bot.send_message(
                uid,
                "🔐 <b>2-Step Verification is enabled!</b>\n\n"
                "Please enter your 2-Step Verification password:"
            )
        except Exception as e:
            bot.send_message(uid, f"❌ Error: <code>{str(e)}</code>")
            update_user(uid, state="")
            _checker_setup.pop(uid, None)
            try:
                run_async(client.disconnect())
            except Exception:
                pass
        return

    if state == "setup_checker_password":
        password = text.strip()
        setup_data = _checker_setup.get(uid)
        if not setup_data:
            bot.send_message(uid, "❌ Session expired or not found. Please start over.")
            update_user(uid, state="")
            return
        
        client = setup_data["client"]
        
        bot.send_message(uid, "⏳ Verifying 2FA password...")
        try:
            run_async(client.sign_in(password=password))
            bot.send_message(uid, "🎉 <b>Checker Setup Successful!</b>\n\nTelethon session has been saved and activated.")
            update_user(uid, state="")
            _checker_setup.pop(uid, None)
            run_async(client.disconnect())
        except Exception as e:
            bot.send_message(uid, f"❌ Error: <code>{str(e)}</code>")
            update_user(uid, state="")
            _checker_setup.pop(uid, None)
            try:
                run_async(client.disconnect())
            except Exception:
                pass
        return

    # Country search
    if state == "awaiting_country_search":
        s     = u.get("buy_session", {})
        prov  = s.get("prov_letter", "A")
        query = text.strip().lower()
        update_user(uid, state="")
        _search_countries(uid, msg, prov, query)
        return

    # Quantity
    if state == "awaiting_quantity":
        s = u.get("buy_session", {})
        try:
            qty = int(text)
            if qty < 1 or qty > 50:
                raise ValueError
        except ValueError:
            bot.send_message(uid, "❌ Please enter a number between 1 and 50.")
            return
        s["quantity"] = qty
        update_user(uid, buy_session=s, last_buy_session=s, state="")
        if "operator" not in s:
            s["operator"] = "any"
        if "allowed_prices" not in s:
            s["allowed_prices"] = []
            
        update_user(uid, buy_session=s, last_buy_session=s, state="")
        
        op_label = s["operator"]
        allowed = s.get("allowed_prices", [])
        if allowed:
            price_label = ", ".join([f"${x}" for x in allowed])
            price_btn_label = f"💲 Price: {len(allowed)} selected"
        else:
            price_label = "Any Price"
            price_btn_label = "💲 Price: ANY"

        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            types.InlineKeyboardButton("✅ Buy Now",   callback_data="confirm_order"),
        )
        kb.add(
            types.InlineKeyboardButton(f"📡 Operator: {op_label.upper()}", callback_data="select_operator"),
            types.InlineKeyboardButton(price_btn_label, callback_data="select_price_menu"),
        )
        kb.add(types.InlineKeyboardButton("❌ Cancel", callback_data="back_main"))
        countries = get_countries(s.get("prov_letter", "A"))
        services  = get_services(s.get("prov_letter", "A"))
        cname = countries.get(str(s.get("country", "")), {}).get("name", "?")
        sname = services.get(s.get("service", ""), s.get("service", "?"))
        
        # Always reuse or send fresh message using upsend to keep chat clean
        upsend(
            uid,
            f"📦 <b>Confirm Order</b>\n\n"
            f"📱 Service: <b>{sname}</b>\n"
            f"🌍 Country: <b>{flag(cname)} {cname}</b>\n"
            f"🔢 Quantity: <b>{qty}</b>\n"
            f"📡 Selected Operator: <b>{op_label.upper()}</b>\n"
            f"💲 Selected Prices: <b>{price_label}</b>\n\n"
            f"⚠️ Balance will be deducted upon purchase.",
            reply_markup=kb,
        )
        return

    # Max Price
    if state == "awaiting_max_price":
        s = u.get("buy_session", {})
        try:
            max_p = float(text)
        except ValueError:
            bot.send_message(uid, "❌ Enter a valid number (e.g. 0.15)")
            return
        s["max_price"] = max_p
        update_user(uid, buy_session=s, state="awaiting_quantity")
        bot.send_message(uid, f"✅ Max Price set to ${max_p}\n\nHow many numbers do you want? (1–50)")
        return

    # ─── Reply Keyboard Button Handlers (mirrors Main Menu) ───
    if not is_logged_in(uid):
        bot.send_message(uid, "⚠️ Please login first. Send /start")
        return

    if text == "💰 Balance":
        _del(uid, msg.message_id)   # delete user's button press
        api2   = get_api(uid)
        result = api2.balance()
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            types.InlineKeyboardButton("🔄 Refresh", callback_data="balance"),
            types.InlineKeyboardButton("⬅️ Back",    callback_data="back_main"),
        )
        if "balance" in result:
            upsend(
                uid,
                f"💰 <b>Your Balance</b>\n\n"
                f"💵 Amount: <b>${result['balance']}</b>\n"
                f"💱 Currency: {result.get('currency','USD')}\n\n"
                f"🕒 Updated: {datetime.now().strftime('%H:%M:%S')}",
                reply_markup=kb,
            )
        else:
            upsend(uid, "❌ Failed to fetch balance.", reply_markup=kb)
        return

    if text == "📱 Buy Number":
        _del(uid, msg.message_id)   # delete user's button press
        m = bot.send_message(uid, "📱 <b>Buy Number</b>\n\nSelect a Provider:", reply_markup=provider_keyboard(), parse_mode="HTML")
        _session_msg[uid] = m.message_id
        update_user(uid, session_msg_id=m.message_id)
        return

    if text == "📋 My Orders":
        _del(uid, msg.message_id)   # delete user's button press
        _show_my_orders_msg(uid)
        return

    if text == "📜 History":
        _del(uid, msg.message_id)   # delete user's button press
        _show_history_msg(uid)
        return

    if text == "🔑 Change API Key":
        _del(uid, msg.message_id)   # delete user's button press
        update_user(uid, state="awaiting_key")
        upsend(uid, "🔑 Please send your new API Key:")
        return

    if text == "❌ Logout":
        _del(uid, msg.message_id)   # delete user's button press
        set_user(uid, {})
        _session_msg.pop(uid, None)
        bot.send_message(
            uid,
            "👋 You have been logged out.\nUse /start to login again.",
            reply_markup=types.ReplyKeyboardRemove(),
        )
        return

    if text == "⚙️ Setup Checker":
        _del(uid, msg.message_id)
        session_path = os.path.join(BASE_DIR, 'checker_session.session')
        if os.path.exists(session_path):
            bot.send_message(
                uid,
                "⚠️ <b>Checker Already Active!</b>\n\n"
                "There is already a checker configured. Please remove the existing checker first by clicking <b>Remove Checker</b>."
            )
            return
        update_user(uid, state="setup_checker_phone")
        bot.send_message(
            uid,
            "📱 <b>Setup Checker</b>\n\n"
            "Please send the phone number of the Telegram account you want to use for checking (e.g. <code>+88017XXXXXXXX</code>):"
        )
        return

    if text == "🗑️ Remove Checker":
        _del(uid, msg.message_id)
        session_path = os.path.join(BASE_DIR, 'checker_session.session')
        if os.path.exists(session_path):
            try:
                os.remove(session_path)
                bot.send_message(uid, "🗑️ <b>Checker Removed!</b>\n\nTelethon session file has been deleted.")
            except Exception as e:
                bot.send_message(uid, f"❌ Error deleting session: <code>{str(e)}</code>")
        else:
            bot.send_message(uid, "ℹ️ Checker session is not active or already removed.")
        return

    bot.send_message(uid, "ℹ️ Use /menu or the buttons below.")

# ─────────────── Callback Handler ───────────────
@bot.callback_query_handler(func=lambda c: True)
def handle_callback(call: types.CallbackQuery):
    uid  = call.from_user.id
    data = call.data
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass
    # NOTE: _session_msg is managed exclusively by upsend() — do NOT set it here.
    # Callbacks always edit call.message.message_id directly.

    if not is_logged_in(uid) and data not in ("back_main",):
        bot.send_message(uid, "⚠️ Please login first with /start")
        return

    if data == "balance":
        _show_balance(uid, call.message)

    elif data == "buy_menu":
        bot.edit_message_text(
            "📱 <b>Buy Number</b>\n\nSelect a Provider:",
            uid, call.message.message_id,
            reply_markup=provider_keyboard(),
        )

    elif data.startswith("prov_"):
        prov = data.split("_")[1]
        update_user(uid, buy_session={"provider": PROVIDERS[prov], "prov_letter": prov})
        _show_services(uid, call.message, prov)

    elif data.startswith("svc_"):
        parts = data.split("_", 2)
        prov, svc_code = parts[1], parts[2]
        s = get_user(uid).get("buy_session", {})
        s["service"] = svc_code
        update_user(uid, buy_session=s)
        _show_countries(uid, call.message, prov, page=0)

    elif data.startswith("cpage_"):
        parts = data.split("_")
        prov, page = parts[1], int(parts[2])
        _show_countries(uid, call.message, prov, page)

    elif data.startswith("csearch_"):
        prov = data.split("_")[1]
        update_user(uid, state="awaiting_country_search")
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("❌ Cancel", callback_data=f"cpage_{prov}_0"))
        bot.send_message(
            uid,
            f"🔍 <b>Search Country</b> (Provider {prov})\n\n"
            f"Type a country name or part of it:\n"
            f"<i>Example: Bangladesh, USA, India...</i>",
            reply_markup=kb,
        )

    elif data.startswith("ctry_"):
        parts = data.split("_", 2)
        prov, cid = parts[1], parts[2]
        s = get_user(uid).get("buy_session", {})
        s["country"] = int(cid)
        update_user(uid, buy_session=s)
        _show_offers(uid, call.message, s)

    elif data.startswith("qty_"):
        val = data.split("_")[1]
        s   = get_user(uid).get("buy_session", {})
        if val == "custom":
            update_user(uid, state="awaiting_quantity", buy_session=s)
            bot.send_message(uid, "✏️ How many numbers do you want? (1–50)")
        else:
            qty = int(val)
            s["quantity"] = qty
            # Initialize operator and allowed_prices if not set
            if "operator" not in s:
                s["operator"] = "any"
            if "allowed_prices" not in s:
                s["allowed_prices"] = []
            # ✅ Save session so user can quickly buy again with same settings
            update_user(uid, buy_session=s, last_buy_session=s)
            
            op_label = s["operator"]
            allowed = s.get("allowed_prices", [])
            if allowed:
                price_label = ", ".join([f"${x}" for x in allowed])
                price_btn_label = f"💲 Price: {len(allowed)} selected"
            else:
                price_label = "Any Price"
                price_btn_label = "💲 Price: ANY"

            kb = types.InlineKeyboardMarkup(row_width=2)
            kb.add(
                types.InlineKeyboardButton("✅ Buy Now",   callback_data="confirm_order"),
            )
            kb.add(
                types.InlineKeyboardButton(f"📡 Operator: {op_label.upper()}", callback_data="select_operator"),
                types.InlineKeyboardButton(price_btn_label, callback_data="select_price_menu"),
            )
            kb.add(types.InlineKeyboardButton("❌ Cancel", callback_data="back_main"))
            countries_d = get_countries(s.get("prov_letter", "A"))
            services_d  = get_services(s.get("prov_letter", "A"))
            cname_d = countries_d.get(str(s.get("country", "")), {}).get("name", "?")
            sname_d = services_d.get(s.get("service", ""), s.get("service", "?"))
            bot.edit_message_text(
                f"📦 <b>Confirm Order</b>\n\n"
                f"📱 Service: <b>{sname_d}</b>\n"
                f"🌍 Country: <b>{flag(cname_d)} {cname_d}</b>\n"
                f"🔢 Quantity: <b>{qty}</b>\n"
                f"📡 Selected Operator: <b>{op_label.upper()}</b>\n"
                f"💲 Selected Prices: <b>{price_label}</b>\n\n"
                f"⚠️ Balance will be deducted upon purchase.",
                uid, call.message.message_id,
                reply_markup=kb,
            )

    elif data == "confirm_order":
        s = get_user(uid).get("buy_session", {})
        # ✅ Session expired / already used → show friendly error
        if not s or not s.get("provider") or not s.get("service") or not s.get("country"):
            bot.send_message(
                uid,
                "⚠️ <b>এই অর্ডার আর করা যাবে না!</b>\n\n"
                "নম্বর আগেই কেনা হয়ে গেছে অথবা session শেষ হয়ে গেছে।\n"
                "নতুন নম্বর কিনতে 📱 <b>Buy Number</b> ব্যবহার করুন।",
            )
            return
        # ✅ Instantly remove buttons from Confirm Order message on first click
        # This prevents double-clicks from ever reaching the order logic.
        try:
            bot.edit_message_reply_markup(uid, call.message.message_id, reply_markup=types.InlineKeyboardMarkup())
        except Exception:
            pass
        qty = s.get("quantity", 1)
        confirm_msg_id = call.message.message_id
        if qty > 1:
            threading.Thread(target=_do_bulk_order, args=(uid, s, confirm_msg_id), daemon=True).start()
        else:
            threading.Thread(target=_do_order, args=(uid, s, confirm_msg_id), daemon=True).start()

    elif data.startswith("bulk_cancel_"):
        group_id = data.split("_", 2)[2]
        _bulk_cancel(uid, call.message, group_id)

    elif data.startswith("bulk_stop_"):
        group_id = data.split("_", 2)[2]
        _cancel_flags.add(group_id)

    elif data.startswith("bulk_again_"):
        group_id = data.split("_", 2)[2]
        threading.Thread(target=_bulk_cancel_and_again, args=(uid, call.message, group_id), daemon=True).start()

    elif data == "set_max_price":
        update_user(uid, state="awaiting_max_price")
        bot.send_message(uid, "💬 Enter max price per number (e.g. <code>0.15</code>):")

    elif data == "select_operator":
        # Dynamic operator list selection screen
        s = get_user(uid).get("buy_session", {})
        prov_letter = s.get("prov_letter", "A")
        api = get_api(uid)
        offers_data = api.offers(prov_letter, s.get("service", ""), s.get("country", 0))
        
        operators = ["any"]
        if offers_data and "offers" in offers_data:
            for o in offers_data["offers"]:
                op = o.get("operator")
                if op and op not in operators:
                    operators.append(op)
                    
        kb = types.InlineKeyboardMarkup(row_width=2)
        btns = []
        for op in operators:
            btns.append(types.InlineKeyboardButton(op.upper(), callback_data=f"set_operator_{op}"))
        kb.add(*btns)
        
        # Back button redirect logic
        back_target = f"qty_{s.get('quantity')}" if s.get("quantity") else "back_to_offers"
        kb.add(types.InlineKeyboardButton("⬅️ Back", callback_data=back_target))
        
        try:
            bot.edit_message_text(
                "📡 <b>Select an Operator</b>\n\nChoose from available operators or select ANY:",
                uid, call.message.message_id, reply_markup=kb, parse_mode="HTML"
            )
        except Exception:
            pass

    elif data == "back_to_offers":
        s = get_user(uid).get("buy_session", {})
        _show_offers(uid, call.message, s)

    elif data.startswith("set_operator_"):
        op = data.split("_", 2)[2]
        s = get_user(uid).get("buy_session", {})
        s["operator"] = op
        update_user(uid, buy_session=s, last_buy_session=s)
        # Redirect back to appropriate screen
        if s.get("quantity"):
            call.data = f"qty_{s.get('quantity')}"
        else:
            call.data = "back_to_offers"
        handle_callback(call)

    elif data == "select_price_menu":
        # Dynamic price list selection screen (Multi-select)
        s = get_user(uid).get("buy_session", {})
        prov_letter = s.get("prov_letter", "A")
        api = get_api(uid)
        offers_data = api.offers(prov_letter, s.get("service", ""), s.get("country", 0))
        
        # Get active selection list
        allowed = s.get("allowed_prices", [])
        if not isinstance(allowed, list):
            allowed = []
            
        prices = []
        if offers_data and "offers" in offers_data:
            for o in offers_data["offers"]:
                pr = o.get("price")
                if pr:
                    try:
                        prices.append(round(float(pr), 4))
                    except ValueError:
                        pass
        
        prices = sorted(list(set(prices)))
        
        kb = types.InlineKeyboardMarkup(row_width=3)
        btns = []
        
        # Any Price Button
        any_label = "✅ Any Price" if not allowed else "Any Price"
        btns.append(types.InlineKeyboardButton(any_label, callback_data="set_price_any"))
        
        for pr in prices[:15]:  # limit to top 15 prices
            is_selected = pr in allowed
            label = f"✅ ${pr}" if is_selected else f"${pr}"
            btns.append(types.InlineKeyboardButton(label, callback_data=f"toggle_price_{pr}"))
            
        kb.add(*btns)
        
        back_target = f"qty_{s.get('quantity')}" if s.get("quantity") else "back_to_offers"
        kb.add(
            types.InlineKeyboardButton("✨ Done / Save", callback_data=back_target),
            types.InlineKeyboardButton("⬅️ Cancel", callback_data=back_target)
        )
        
        selected_text = ", ".join([f"${x}" for x in allowed]) if allowed else "Any Price"
        try:
            bot.edit_message_text(
                f"💲 <b>Select Price Limits (Multi-Select)</b>\n\n"
                f"Click on the rates you want to buy. Click again to unselect.\n"
                f"Currently Selected: <b>{selected_text}</b>",
                uid, call.message.message_id, reply_markup=kb, parse_mode="HTML"
            )
        except Exception:
            pass

    elif data.startswith("toggle_price_"):
        val = float(data.split("_")[2])
        s = get_user(uid).get("buy_session", {})
        allowed = s.get("allowed_prices", [])
        if not isinstance(allowed, list):
            allowed = []
            
        if val in allowed:
            allowed.remove(val)
        else:
            allowed.append(val)
            
        s["allowed_prices"] = allowed
        # Clear single max_price since we are using list
        s["max_price"] = None
        update_user(uid, buy_session=s, last_buy_session=s)
        
        # Reload selection screen
        call.data = "select_price_menu"
        handle_callback(call)

    elif data == "set_price_any":
        s = get_user(uid).get("buy_session", {})
        s["allowed_prices"] = []
        s["max_price"] = None
        update_user(uid, buy_session=s, last_buy_session=s)
        
        # Reload selection screen
        call.data = "select_price_menu"
        handle_callback(call)

    elif data == "my_orders":
        _show_my_orders(uid, call.message)

    elif data.startswith("order_"):
        oid = data.split("_")[1]
        _show_order_detail(uid, call.message, oid)

    elif data.startswith("check_"):
        oid = data.split("_")[1]
        _check_otp(uid, call.message, oid)

    elif data.startswith("cancel_"):
        oid = data.split("_")[1]
        _cancel_order(uid, call.message, oid)

    elif data.startswith("resend_"):
        oid = data.split("_")[1]
        _resend_otp(uid, call.message, oid)

    elif data.startswith("fav_add_"):
        # fav_add_A_187
        parts = data.split("_", 3)
        prov, cid = parts[2], parts[3]
        add_fav_country(uid, prov, cid)
        countries = get_countries(prov)
        cname = countries.get(cid, {}).get("name", cid)
        bot.send_message(uid, f"⭐ <b>{flag(cname)} {cname}</b> Favorites-এ যোগ হয়েছে!")
        s = get_user(uid).get("buy_session", {})
        _show_offers(uid, call.message, s)

    elif data.startswith("fav_del_"):
        # fav_del_A_187
        parts = data.split("_", 3)
        prov, cid = parts[2], parts[3]
        remove_fav_country(uid, prov, cid)
        countries = get_countries(prov)
        cname = countries.get(cid, {}).get("name", cid)
        bot.send_message(uid, f"❌ <b>{flag(cname)} {cname}</b> Favorites থেকে বাদ দেওয়া হয়েছে।")
        s = get_user(uid).get("buy_session", {})
        _show_offers(uid, call.message, s)

    elif data == "history":
        _show_history(uid, call.message)

    elif data == "change_key":
        update_user(uid, state="awaiting_key")
        bot.send_message(uid, "🔑 Please send your new API Key:")

    elif data == "logout":
        set_user(uid, {})
        bot.edit_message_text(
            "👋 You have been logged out.\nUse /start to login again.",
            uid, call.message.message_id,
        )

    elif data == "admin" and uid == ADMIN_ID:
        _show_admin(uid, call.message)

    elif data == "setup_checker":
        session_path = os.path.join(BASE_DIR, 'checker_session.session')
        if os.path.exists(session_path):
            bot.send_message(
                uid,
                "⚠️ <b>Checker Already Active!</b>\n\n"
                "There is already a checker configured. Please remove the existing checker first by clicking <b>Remove Checker</b>."
            )
            return
        update_user(uid, state="setup_checker_phone")
        bot.send_message(
            uid,
            "📱 <b>Setup Checker</b>\n\n"
            "Please send the phone number of the Telegram account you want to use for checking (e.g. <code>+88017XXXXXXXX</code>):"
        )

    elif data == "remove_checker":
        session_path = os.path.join(BASE_DIR, 'checker_session.session')
        if os.path.exists(session_path):
            try:
                os.remove(session_path)
                bot.send_message(uid, "🗑️ <b>Checker Removed!</b>\n\nTelethon session file has been deleted.")
            except Exception as e:
                bot.send_message(uid, f"❌ Error deleting session: <code>{str(e)}</code>")
        else:
            bot.send_message(uid, "ℹ️ Checker session is not active or already removed.")

    elif data == "admin_unset_group" and uid == ADMIN_ID:
        global FORWARD_GROUP_ID
        FORWARD_GROUP_ID = None
        CONFIG["forward_group_id"] = None
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(CONFIG, f, ensure_ascii=False, indent=2)
        _show_admin(uid, call.message)  # refresh admin panel

    elif data == "back_main":
        bot.edit_message_text(
            "🏠 <b>Main Menu</b>",
            uid, call.message.message_id,
            reply_markup=main_menu_keyboard(uid),
        )
    elif data == "back_buy":
        bot.edit_message_text(
            "📱 <b>Buy Number</b>\n\nSelect a Provider:",
            uid, call.message.message_id,
            reply_markup=provider_keyboard(),
        )

    elif data == "buy_again_last":
        # ✅ Directly buy with last session — no confirm screen
        u2 = get_user(uid)
        s  = u2.get("last_buy_session") or u2.get("last_bulk_session", {})
        # Require ALL fields — provider alone is not enough
        if not s or not s.get("provider") or not s.get("service") or not s.get("country"):
            bot.send_message(uid, "⚠️ No previous session found. Please select from Buy Number.")
            bot.send_message(uid, "📱 <b>Buy Number</b>\n\nSelect a Provider:", reply_markup=provider_keyboard())
            return
        # Restore session and order immediately
        update_user(uid, buy_session=s)
        qty = s.get("quantity", 1)
        if qty > 1:
            threading.Thread(target=_do_bulk_order, args=(uid, s), daemon=True).start()
        else:
            threading.Thread(target=_do_order, args=(uid, s), daemon=True).start()

# ─────────────── Balance ───────────────
def _show_balance(uid: int, msg: types.Message):
    api    = get_api(uid)
    result = api.balance()
    if "balance" in result:
        text = (
            f"💰 <b>Your Balance</b>\n\n"
            f"💵 Amount: <b>${result['balance']}</b>\n"
            f"💱 Currency: {result.get('currency','USD')}\n\n"
            f"🕒 Updated: {datetime.now().strftime('%H:%M:%S')}"
        )
    else:
        text = f"❌ Failed to load balance.\n<code>{result}</code>"
    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton("🔄 Refresh", callback_data="balance"),
        types.InlineKeyboardButton("⬅️ Back",    callback_data="back_main"),
    )
    bot.edit_message_text(text, uid, msg.message_id, reply_markup=kb)

# ─────────────── Services ───────────────
POPULAR_SERVICES = ["tg", "wa", "fb", "ig", "go", "tw", "ds", "dr", "mm", "am", "lf", "ub"]

def _show_services(uid: int, msg: types.Message, prov: str):
    services = get_services(prov)
    if not services:
        bot.send_message(uid, "❌ Failed to load services.")
        return
    kb = types.InlineKeyboardMarkup(row_width=2)
    popular = []
    for code in POPULAR_SERVICES:
        if code in services:
            name = services[code]
            popular.append(types.InlineKeyboardButton(
                f"{service_emoji(name)} {name[:18]}", callback_data=f"svc_{prov}_{code}"
            ))
    kb.add(*popular)
    others, count = [], 0
    for code, name in services.items():
        if code not in POPULAR_SERVICES and count < 20:
            others.append(types.InlineKeyboardButton(
                f"{service_emoji(name)} {name[:18]}", callback_data=f"svc_{prov}_{code}"
            ))
            count += 1
    if others:
        kb.add(*others)
    kb.add(types.InlineKeyboardButton("⬅️ Back", callback_data="back_buy"))
    bot.edit_message_text(
        f"📱 <b>Select Service</b> (Provider {prov})\n\n"
        "⭐ Popular services shown first:",
        uid, msg.message_id, reply_markup=kb,
    )

# ─────────────── Countries ───────────────
COUNTRIES_PER_PAGE  = 20
POPULAR_COUNTRIES   = [187, 16, 22, 60, 66, 10, 6, 52, 7, 4, 43, 78, 86, 56, 73]

def _show_countries(uid: int, msg: types.Message, prov: str, page: int):
    countries = get_countries(prov)
    if not countries:
        bot.send_message(uid, "❌ Failed to load countries.")
        return

    fav_ids = get_fav_countries(uid, prov)   # list of str cids

    if page == 0:
        # ⭐ Favorites first, then popular, then the rest
        fav_items     = [(k, v) for k, v in countries.items() if k in fav_ids]
        popular_items = [(str(k), v) for k, v in countries.items()
                        if int(k) in POPULAR_COUNTRIES and k not in fav_ids]
        rest          = [(k, v) for k, v in countries.items()
                        if int(k) not in POPULAR_COUNTRIES and k not in fav_ids]
        items         = fav_items + popular_items + rest
    else:
        items = [(k, v) for k, v in countries.items()
                if int(k) not in POPULAR_COUNTRIES and k not in fav_ids]

    total      = len(items)
    start      = page * COUNTRIES_PER_PAGE
    end        = start + COUNTRIES_PER_PAGE
    page_items = items[start:end]

    kb   = types.InlineKeyboardMarkup(row_width=2)
    btns = []
    for cid, cdata in page_items:
        cname  = cdata["name"]
        prefix = "⭐ " if cid in fav_ids else ""
        btns.append(types.InlineKeyboardButton(
            f"{prefix}{flag(cname)} {cname}", callback_data=f"ctry_{prov}_{cid}"
        ))
    kb.add(*btns)

    nav = []
    if page > 0:
        nav.append(types.InlineKeyboardButton("◀️ Prev", callback_data=f"cpage_{prov}_{page-1}"))
    if end < total:
        nav.append(types.InlineKeyboardButton("Next ▶️", callback_data=f"cpage_{prov}_{page+1}"))
    if nav:
        kb.add(*nav)
    kb.add(
        types.InlineKeyboardButton("🔍 Search Country", callback_data=f"csearch_{prov}"),
        types.InlineKeyboardButton("⬅️ Back",           callback_data=f"prov_{prov}"),
    )

    header = ""
    if fav_ids and page == 0:
        header = f"⭐ Your favorites are shown first (marked ⭐)\n"
    bot.edit_message_text(
        f"🌍 <b>Select Country</b> (Provider {prov})\n"
        f"Page {page+1} / {(total // COUNTRIES_PER_PAGE) + 1} — {total} countries total\n"
        f"{header}"
        f"⚡ <i>Use 🔍 Search to find a specific country</i>",
        uid, msg.message_id, reply_markup=kb,
    )

# ─────────────── Country Search ───────────────
def _search_countries(uid: int, msg: types.Message, prov: str, query: str):
    countries = get_countries(prov)
    if not countries:
        bot.send_message(uid, "❌ Failed to load countries.")
        return

    matched = [(cid, cdata) for cid, cdata in countries.items() if query in cdata["name"].lower()]

    if not matched:
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            types.InlineKeyboardButton("🔍 Search Again", callback_data=f"csearch_{prov}"),
            types.InlineKeyboardButton("🌍 All Countries", callback_data=f"cpage_{prov}_0"),
        )
        bot.send_message(
            uid,
            f"❌ <b>No country found for '{query}'.</b>\n\nTry again or browse all countries.",
            reply_markup=kb,
        )
        return

    kb   = types.InlineKeyboardMarkup(row_width=2)
    btns = []
    for cid, cdata in matched[:30]:
        cname = cdata["name"]
        btns.append(types.InlineKeyboardButton(
            f"{flag(cname)} {cname}", callback_data=f"ctry_{prov}_{cid}"
        ))
    kb.add(*btns)
    kb.add(
        types.InlineKeyboardButton("🔍 Search Again",  callback_data=f"csearch_{prov}"),
        types.InlineKeyboardButton("🌍 All Countries", callback_data=f"cpage_{prov}_0"),
    )
    bot.send_message(
        uid,
        f"🔍 <b>Search results for '{query}'</b> ({len(matched)} found)\n\nSelect a country:",
        reply_markup=kb,
    )

# ─────────────── Offers ───────────────
def _show_offers(uid: int, msg: types.Message, s: dict):
    prov_letter = s.get("prov_letter", "A")
    api         = get_api(uid)
    offers_data = api.offers(prov_letter, s["service"], s["country"])
    countries   = get_countries(prov_letter)
    services    = get_services(prov_letter)
    cname = countries.get(str(s["country"]), {}).get("name", str(s["country"]))
    sname = services.get(s["service"], s["service"])
    cid_str = str(s["country"])

    text = (
        f"📋 <b>Order Details</b>\n\n"
        f"🏢 Provider: <b>Provider {prov_letter}</b>\n"
        f"📱 Service: <b>{sname}</b>\n"
        f"🌍 Country: <b>{flag(cname)} {cname}</b>\n\n"
    )

    if offers_data and "offers" in offers_data:
        offers = offers_data["offers"]
        if offers:
            best = min(offers, key=lambda x: float(x.get("price", 999)))
            text += (
                f"💰 Best Price: <b>${best.get('price','?')}</b>\n"
                f"📦 Available: <b>{best.get('available','?')} pcs</b>\n"
                f"📡 Operator: {best.get('operator','any')}\n\n"
            )
        else:
            text += "⚠️ No numbers available right now.\n\n"
    elif isinstance(offers_data, list) and offers_data:
        for item in offers_data:
            for offer in item.get("offers", []):
                text += f"💰 Price: <b>${offer.get('price','?')}</b> | 📦 {offer.get('count','?')} pcs\n"
        text += "\n"
    else:
        text += "⚠️ Could not load offers.\n\n"

    text += "👇 <b>How many numbers do you want?</b>"

    # ⭐ Favorites toggle
    fav_ids = get_fav_countries(uid, prov_letter)
    is_fav  = cid_str in fav_ids
    fav_btn = types.InlineKeyboardButton(
        f"❌ Remove {cname} from Favorites" if is_fav else f"⭐ Add {cname} to Favorites",
        callback_data=f"fav_del_{prov_letter}_{cid_str}" if is_fav else f"fav_add_{prov_letter}_{cid_str}",
    )

    kb = types.InlineKeyboardMarkup(row_width=4)
    kb.add(
        types.InlineKeyboardButton("1️⃣ 1",  callback_data="qty_1"),
        types.InlineKeyboardButton("3️⃣ 3",  callback_data="qty_3"),
        types.InlineKeyboardButton("5️⃣ 5",  callback_data="qty_5"),
        types.InlineKeyboardButton("🔟 10", callback_data="qty_10"),
    )
    op_label = s.get("operator", "any")
    allowed = s.get("allowed_prices", [])
    price_btn_label = f"💲 Price: {len(allowed)} selected" if allowed else "💲 Price: ANY"

    kb.add(
        types.InlineKeyboardButton("✏️ Custom",    callback_data="qty_custom"),
    )
    kb.add(
        types.InlineKeyboardButton(f"📡 Operator: {op_label.upper()}", callback_data="select_operator"),
        types.InlineKeyboardButton(price_btn_label, callback_data="select_price_menu"),
    )
    kb.add(fav_btn)
    kb.add(types.InlineKeyboardButton("⬅️ Back", callback_data=f"cpage_{prov_letter}_0"))
    bot.edit_message_text(text, uid, msg.message_id, reply_markup=kb)

# ─────────────── Single Order ───────────────
MAX_PRICE_RETRY_INTERVAL = 10   # seconds between retries
MAX_PRICE_RETRY_LIMIT    = 180  # max retries (~30 minutes)

def _do_order(uid: int, s: dict, msg_id: int = None):
    api       = get_api(uid)
    max_price = s.get("max_price")
    services  = get_services(s.get("prov_letter", "A"))
    countries = get_countries(s.get("prov_letter", "A"))
    sname     = services.get(s.get("service", ""), s.get("service", "?"))
    cname     = countries.get(str(s.get("country", "")), {}).get("name", "?")

    kb_cancel = types.InlineKeyboardMarkup()
    kb_cancel.add(types.InlineKeyboardButton("❌ Stop Searching", callback_data="back_main"))

    def _set_status(text: str, markup=None):
        """Edit msg_id if available, else send new and remember the id."""
        nonlocal msg_id
        if msg_id:
            try:
                bot.edit_message_text(text, uid, msg_id, reply_markup=markup)
                return
            except Exception:
                pass
        m = bot.send_message(uid, text, reply_markup=markup)
        msg_id = m.message_id

    allowed_prices = s.get("allowed_prices", [])
    if not isinstance(allowed_prices, list):
        allowed_prices = []
    
    # Use max of allowed_prices as API max_price hint if list is not empty
    api_max = max(allowed_prices) if allowed_prices else None

    if allowed_prices:
        p_str = ", ".join([f"${x}" for x in allowed_prices])
        _set_status(
            f"🔍 <b>Searching for number...</b>\n\n"
            f"📱 Service: <b>{sname}</b>\n"
            f"🌍 Country: <b>{flag(cname)} {cname}</b>\n"
            f"💲 Selected Prices: <b>{p_str}</b>\n\n"
            f"<i>⏳ Waiting for a number at selected prices. Will keep trying automatically...</i>\n"
            f"<code>Attempt 1 — searching...</code>",
            markup=kb_cancel,
        )
    elif max_price:
        _set_status(
            f"🔍 <b>Searching for number...</b>\n\n"
            f"📱 Service: <b>{sname}</b>\n"
            f"🌍 Country: <b>{flag(cname)} {cname}</b>\n"
            f"💲 Max Price: <b>${max_price}</b>\n\n"
            f"<i>⏳ Waiting for a number at or below max price. Will keep trying automatically...</i>\n"
            f"<code>Attempt 1 — searching...</code>",
            markup=kb_cancel,
        )
    else:
        _set_status("⏳ Ordering number...")

    attempt = 0
    while True:
        attempt += 1
        try:
            result = api.order_number_with_fallback(
                provider=s["provider"], service=s["service"],
                country=s["country"],
                allowed_prices=allowed_prices,
                max_price=max_price,
                operator=s.get("operator")
            )
        except Exception as e:
            result = {"error": str(e)}

        number   = result.get("number") or result.get("phone") or result.get("num") or result.get("telephone")
        order_id = str(result.get("id") or result.get("order_id") or result.get("activation_id") or "")

        if number and order_id:
            price_raw = result.get("price", 0)
            try:
                price_val = float(price_raw)
            except Exception:
                price_val = 0.0

            # Verify against allowed_prices list or max_price
            price_matched = True
            if allowed_prices:
                # Find matching price within a small float tolerance (e.g. 0.005)
                price_matched = any(abs(price_val - x) < 0.005 for x in allowed_prices)
            elif max_price:
                price_matched = price_val <= float(max_price)

            if not price_matched:
                # Cancel this unqualified number silently
                try:
                    api.cancel_number(int(order_id))
                except Exception:
                    pass
                err_hint = f"Price ${price_val:.4f} not in allowed list — cancelled, retrying..."
                if attempt >= MAX_PRICE_RETRY_LIMIT:
                    _set_status(
                        f"⏰ <b>Search Timed Out</b>\n\n"
                        f"Could not find a number matching price criteria after {attempt} attempts.",
                        markup=back_keyboard("main"),
                    )
                    return
                
                label_val = ", ".join([f"${x}" for x in allowed_prices]) if allowed_prices else f"${max_price}"
                _set_status(
                    f"🔍 <b>Searching for number...</b>\n\n"
                    f"📱 Service: <b>{sname}</b>\n"
                    f"🌍 Country: <b>{flag(cname)} {cname}</b>\n"
                    f"💲 Target Prices: <b>{label_val}</b>\n\n"
                    f"<i>⏳ Retrying every {MAX_PRICE_RETRY_INTERVAL}s...</i>\n"
                    f"<code>Attempt {attempt} — {err_hint}</code>",
                    markup=kb_cancel,
                )
                time.sleep(MAX_PRICE_RETRY_INTERVAL)
                continue

            # ✅ Price OK — save and notify
            try:
                price = f"{float(price_raw):.4f}"
            except Exception:
                price = str(price_raw)
            u     = get_user(uid)
            orders = u.get("orders", {})
            orders[order_id] = {
                "id": order_id, "number": number,
                "service": s["service"], "provider": s["provider"],
                "country": s["country"], "price": price,
                "status": "active", "created_at": datetime.now().isoformat(), "otp": None,
            }
            update_user(uid, orders=orders, buy_session={}, last_buy_session=s)

            # Check status from @DustOtpBot
            if s.get("service") == "tg":
                check_status = check_number_via_bot(number)
                is_single_checker_error = not check_status or "error" in check_status.lower() or "❌" in check_status
                if not is_single_checker_error:
                    check_display = f"\n\n{check_status}"
                else:
                    if check_status:
                        check_display = f"\n\n⚠️ <b>চেকার এরর হয়েছে! (Checker Error)</b>\n<code>{check_status}</code>"
                    else:
                        check_display = f"\n\n⚠️ <b>চেকার এরর হয়েছে! (Checker session not active)</b>"
            else:
                check_display = ""

            kb = types.InlineKeyboardMarkup(row_width=2)
            kb.add(
                types.InlineKeyboardButton("🔍 Check OTP", callback_data=f"check_{order_id}"),
                types.InlineKeyboardButton("🔄 Resend",    callback_data=f"resend_{order_id}"),
            )
            kb.add(
                types.InlineKeyboardButton("❌ Cancel",      callback_data=f"cancel_{order_id}"),
                types.InlineKeyboardButton("🔄 Buy Again",  callback_data="buy_again_last"),
            )
            order_text = (
                f"✅ <b>Number Received!</b>\n\n"
                f"📞 Number: <code>{number}</code>\n"
                f"🆔 Order ID: <code>{order_id}</code>\n"
                f"💰 Price: ${price}" +
                (f" ✔️ (Max: ${max_price})" if max_price else "") +
                check_display +
                f"\n\n📋 Use this number and press Check OTP when the code arrives.\n"
                f"⏰ Time limit: ~19 minutes"
            )
            _set_status(order_text, markup=kb)
            # ✅ Track this message so auto-poll can edit it instead of sending new
            track_order_msg(uid, order_id, msg_id)
            threading.Thread(target=_auto_poll_otp, args=(uid, order_id), daemon=True).start()
            return

        # If allowed_prices/max_price is empty, we still keep retrying instead of immediately failing.
        # This fixes the bug where ordering without price limit fails on first empty response.

        if attempt >= MAX_PRICE_RETRY_LIMIT:
            _set_status(
                f"⏰ <b>Search Timed Out</b>\n\n"
                f"No number found at or below <b>${max_price}</b> after {attempt} attempts.",
                markup=back_keyboard("main"),
            )
            return

        err_hint = (
            result.get("message") or result.get("error") or
            result.get("detail") or str(result)
        )
        _set_status(
            f"🔍 <b>Searching for number...</b>\n\n"
            f"📱 Service: <b>{sname}</b>\n"
            f"🌍 Country: <b>{flag(cname)} {cname}</b>\n"
            f"💲 Max Price: <b>${max_price}</b>\n\n"
            f"<i>⏳ No number found yet. Retrying every {MAX_PRICE_RETRY_INTERVAL}s...</i>\n"
            f"<code>Attempt {attempt} — {str(err_hint)[:80]}</code>",
            markup=kb_cancel,
        )

        time.sleep(MAX_PRICE_RETRY_INTERVAL)

# ─────────────── Bulk Order ───────────────
def _do_bulk_order(uid: int, s: dict, msg_id: int = None):
    """Buy numbers one by one with 3s gap between each."""
    qty      = s.get("quantity", 1)
    api      = get_api(uid)
    group_id = str(int(time.time()))

    bought, failed = [], []

    services  = get_services(s.get("prov_letter", "A"))
    countries = get_countries(s.get("prov_letter", "A"))
    sname = services.get(s["service"], s["service"])
    cname = countries.get(str(s["country"]), {}).get("name", str(s["country"]))

    kb_stop = types.InlineKeyboardMarkup()
    kb_stop.add(types.InlineKeyboardButton("🛑 Stop Buying", callback_data=f"bulk_stop_{group_id}"))

    # প্রথম progress message — edit existing (Confirm Order) or send new
    prog_text = (
        f"⏳ <b>Starting bulk order...</b>\n"
        f"📱 {sname} | 🌍 {flag(cname)} {cname}\n"
        f"🔢 Total: {qty} numbers\n\n"
        f"<code>0 / {qty} done</code>"
    )
    if msg_id:
        try:
            bot.edit_message_text(prog_text, uid, msg_id, reply_markup=kb_stop)
            progress_msg_id = msg_id
        except Exception:
            progress_msg_id = bot.send_message(uid, prog_text, reply_markup=kb_stop).message_id
    else:
        progress_msg_id = bot.send_message(uid, prog_text, reply_markup=kb_stop).message_id

    stopped_by_user = False
    for i in range(qty):
        # ─── Cancel check ───
        if group_id in _cancel_flags:
            _cancel_flags.discard(group_id)
            stopped_by_user = True
            break
        step = i + 1

        # progress update
        bar = "✅ " * len(bought) + "⏳ " + "⬜ " * (qty - len(bought) - 1)
        try:
            bot.edit_message_text(
                f"⏳ <b>Buying number {step} of {qty}...</b>\n"
                f"📱 {sname} | 🌍 {flag(cname)} {cname}\n\n"
                f"{bar}\n"
                f"<code>{len(bought)} success | {len(failed)} failed</code>",
                uid, progress_msg_id,
                reply_markup=kb_stop,
            )
        except Exception:
            pass

        # ─── একটি নম্বর কেনো ───
        allowed_prices = s.get("allowed_prices", [])
        if not isinstance(allowed_prices, list):
            allowed_prices = []
        max_price = s.get("max_price")
        api_max = max(allowed_prices) if allowed_prices else None

        sub_attempt = 0
        result = None
        while True:
            # ─── Cancel check inside retry loop ───
            if group_id in _cancel_flags:
                _cancel_flags.discard(group_id)
                stopped_by_user = True
                break
            sub_attempt += 1
            try:
                result = api.order_number_with_fallback(
                    s["provider"], s["service"],
                    s["country"],
                    allowed_prices=allowed_prices,
                    max_price=max_price,
                    operator=s.get("operator")
                )
            except Exception as e:
                result = {"error": str(e)}

            _num_check = (
                result.get("number") or result.get("phone") or
                result.get("num") or result.get("telephone")
            )
            _id_check = str(
                result.get("id") or result.get("order_id") or
                result.get("activation_id") or ""
            )

            if _num_check and _id_check:
                # 💲 price check
                try:
                    got_price = float(result.get("price", 0))
                except Exception:
                    got_price = 0.0

                price_matched = True
                if allowed_prices:
                    price_matched = any(abs(got_price - x) < 0.005 for x in allowed_prices)
                elif max_price:
                    price_matched = got_price <= float(max_price)

                if not price_matched:
                    # Cancel this overpriced/invalid number
                    try:
                        api.cancel_number(int(_id_check))
                    except Exception:
                        pass
                    err_hint = f"${got_price:.4f} not allowed — cancelled"
                    if sub_attempt >= MAX_PRICE_RETRY_LIMIT:
                        result = {"error": f"Timed out waiting for matching price"}
                        _num_check = None
                        _id_check  = ""
                        break
                    
                    bar = "✅ " * len(bought) + "🔍 " + "⬜ " * (qty - len(bought) - 1)
                    label_val = ", ".join([f"${x}" for x in allowed_prices]) if allowed_prices else f"${max_price}"
                    try:
                        bot.edit_message_text(
                            f"🔍 <b>Buying number {step} of {qty}...</b>\n"
                            f"📱 {sname} | 🌍 {flag(cname)} {cname}\n"
                            f"💲 Target Prices: {label_val} — retrying...\n\n"
                            f"{bar}\n"
                            f"<code>{len(bought)} success | {len(failed)} failed | attempt {sub_attempt}</code>\n"
                            f"<i>{err_hint}</i>",
                            uid, progress_msg_id,
                            reply_markup=kb_stop,
                        )
                    except Exception:
                        pass
                    time.sleep(MAX_PRICE_RETRY_INTERVAL)
                    continue
                # price OK — break out
                break

            # No premature break here when result is empty, keep retrying up to limit.

            if sub_attempt >= MAX_PRICE_RETRY_LIMIT:
                result = {"error": f"Timed out after {sub_attempt} attempts"}
                break

            # update progress with retry info
            err_hint = (
                result.get("message") or result.get("error") or
                result.get("detail") or str(result)
            )
            
            # 💸 Insufficient Balance / Out of Stock check inside retry loop
            err_lower = err_hint.lower()
            if "balance" in err_lower or "insufficient" in err_lower or "no money" in err_lower or "money" in err_lower:
                result = {"error": err_hint}
                break
            if "no number" in err_lower or "no free" in err_lower or "out of stock" in err_lower or "limit" in err_lower or "no_numbers" in err_lower:
                if len(bought) > 0:
                    result = {"error": err_hint}
                    break

            bar = "✅ " * len(bought) + "🔍 " + "⬜ " * (qty - len(bought) - 1)
            label_val = ", ".join([f"${x}" for x in allowed_prices]) if allowed_prices else f"${max_price}"
            try:
                bot.edit_message_text(
                    f"🔍 <b>Buying number {step} of {qty}...</b>\n"
                    f"📱 {sname} | 🌍 {flag(cname)} {cname}\n"
                    f"💲 Target Prices: {label_val} — retrying...\n\n"
                    f"{bar}\n"
                    f"<code>{len(bought)} success | {len(failed)} failed | attempt {sub_attempt}</code>\n"
                    f"<i>{str(err_hint)[:60]}</i>",
                    uid, progress_msg_id,
                    reply_markup=kb_stop,
                )
            except Exception:
                pass
            time.sleep(MAX_PRICE_RETRY_INTERVAL)
            continue

        if stopped_by_user:
            break

        if not (_num_check and _id_check):
            err_msg = (
                result.get("message") or result.get("error") or
                result.get("detail") or result.get("msg") or str(result)
            )
            failed.append(str(err_msg)[:100])
            
            err_lower = err_msg.lower()
            # 💸 Insufficient Balance check -> Break loop immediately and deliver bought ones
            if "balance" in err_lower or "insufficient" in err_lower or "no money" in err_lower or "money" in err_lower:
                break
                
            # ⚠️ Out of Stock / No numbers -> if we already have some numbers bought, stop and deliver those
            if "no number" in err_lower or "no free" in err_lower or "out of stock" in err_lower or "limit" in err_lower or "no_numbers" in err_lower:
                if len(bought) > 0:
                    break
                    
            if i < qty - 1:
                time.sleep(3)
            continue

        # success — _num_check and _id_check are set from the retry loop
        number   = _num_check
        order_id = _id_check

        raw_price = result.get("price", "?")
        try:
            price = f"{float(raw_price):.4f}"
        except Exception:
            price = str(raw_price)
        u = get_user(uid)
        orders = u.get("orders", {})
        orders[order_id] = {
            "id": order_id, "number": number,
            "service": s["service"], "provider": s["provider"],
            "country": s["country"], "price": price,
            "status": "active",
            "created_at": datetime.now().isoformat(),
            "otp": None, "group_id": group_id,
        }
        update_user(uid, orders=orders)
        bought.append({"id": order_id, "number": number, "price": price})
        # auto OTP poll
        threading.Thread(
            target=_auto_poll_otp, args=(uid, order_id), daemon=True
        ).start()

        # 3 সেকেন্ড বিরতি (শেষেরটার পরে না)
        if i < qty - 1:
            time.sleep(3)


    services  = get_services(s.get("prov_letter", "A"))
    countries = get_countries(s.get("prov_letter", "A"))
    sname = services.get(s["service"], s["service"])
    cname = countries.get(str(s["country"]), {}).get("name", str(s["country"]))

    title_text = "🚫 <b>Bulk Order Stopped!</b>\n" if stopped_by_user else "✅ <b>Bulk Order Complete!</b>\n"

    lines = [
        title_text,
        f"📱 Service: <b>{sname}</b>",
        f"🌍 Country: <b>{flag(cname)} {cname}</b>",
        f"📦 Total: <b>{qty}</b> | Success: <b>{len(bought)}</b> | Failed: <b>{len(failed)}</b>\n",
        "─" * 30,
    ]
    # 2. List of bought numbers (ALWAYS shown)
    total_cost = 0.0
    bought_numbers = []
    for idx, o in enumerate(bought, 1):
        try:
            total_cost += float(o["price"])
            p_val = float(o["price"])
            p_str = f"{p_val:.4f}"
        except Exception:
            p_str = str(o.get("price", "?"))
        num_display = o['number'] if o['number'].startswith('+') else f"+{o['number']}"
        lines.append(f"<b>{idx}.</b> 📞 <code>{num_display}</code>  💰 ${p_str}")
        bought_numbers.append(o["number"])
        
    lines.append("─" * 30)

    if failed:
        lines.append(f"\n❌ <b>{len(failed)} order(s) failed:</b>")
        seen = []
        for err in failed:
            err_str = str(err)[:120]
            if err_str not in seen:
                seen.append(err_str)
                lines.append(f"• <code>{err_str}</code>")
    lines.append(f"\n💸 Total Cost: <b>~${total_cost:.4f}</b>")
    lines.append("\n⏳ You will be notified automatically when OTPs arrive.")

    kb = types.InlineKeyboardMarkup(row_width=2)
    if bought:
        kb.add(
            types.InlineKeyboardButton("❌ Cancel All",  callback_data=f"bulk_cancel_{group_id}"),
            types.InlineKeyboardButton("🔄 Again",       callback_data=f"bulk_again_{group_id}"),
        )
    kb.add(types.InlineKeyboardButton("🔄 Buy Again", callback_data="buy_again_last"))

    try:
        bot.edit_message_text("\n".join(lines), uid, progress_msg_id, reply_markup=kb)
    except Exception:
        bot.send_message(uid, "\n".join(lines), reply_markup=kb)

    # 3. Checker report sent as a SEPARATE message at the bottom
    if s.get("service") == "tg" and bought_numbers:
        bulk_check_report = check_multiple_numbers_via_bot(bought_numbers)
        is_checker_error = not bulk_check_report or "error" in bulk_check_report.lower() or "❌" in bulk_check_report
        
        if not is_checker_error:
            bot.send_message(uid, bulk_check_report)
        else:
            if bulk_check_report:
                bot.send_message(uid, f"⚠️ <b>চেকার এরর হয়েছে! (Checker Error)</b>\n<code>{bulk_check_report}</code>")
            else:
                bot.send_message(uid, "⚠️ <b>চেকার এরর হয়েছে! (Checker session not active)</b>")

    # Save session so 'Again' can reuse it
    update_user(uid, buy_session={}, last_bulk_session=s)

# ─────────────── Bulk Cancel ───────────────
def _bulk_cancel(uid: int, msg: types.Message, group_id: str):
    # ─── Signal running thread to stop ───
    _cancel_flags.add(group_id)

    api    = get_api(uid)
    u      = get_user(uid)
    orders = u.get("orders", {})
    count  = 0
    failed_nums = []

    for oid, o in orders.items():
        if o.get("group_id") == group_id and o.get("status") == "active":
            try:
                result = api.cancel_number(int(oid))
                if _is_cancel_success(result):
                    orders[oid]["status"] = "cancelled"
                    count += 1
                else:
                    err_msg = (result.get("error") or result.get("message") or str(result)) if isinstance(result, dict) else str(result)
                    failed_nums.append(f"+{o.get('number')} ({err_msg[:40]})")
            except Exception as e:
                failed_nums.append(f"+{o.get('number')} ({str(e)[:40]})")
    update_user(uid, orders=orders)

    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("🔄 Buy Again (Same)", callback_data="buy_again_last"),
        types.InlineKeyboardButton("🏠 Menu",           callback_data="back_main"),
    )
    
    status_text = f"✅ <b>{count} number(s) cancelled.</b>\n\n"
    if failed_nums:
        failed_list = "\n".join([f"• <code>{x}</code>" for x in failed_nums])
        status_text += f"⚠️ <b>Failed to cancel ({len(failed_nums)}):</b>\n{failed_list}\n\n"
    status_text += f"🔄 Press <b>Buy Again</b> to reuse same Service + Country + Provider."

    bot.edit_message_text(
        status_text,
        uid, msg.message_id, reply_markup=kb,
    )

# ─────────────── Bulk Cancel & Re-order ───────────────
def _bulk_cancel_and_again(uid: int, msg: types.Message, group_id: str):
    """Cancel all numbers in group, then re-buy with same session."""
    api    = get_api(uid)
    u      = get_user(uid)
    orders = u.get("orders", {})
    count  = 0
    failed_nums = []

    # Cancel all active numbers in this group
    for oid, o in orders.items():
        if o.get("group_id") == group_id and o.get("status") == "active":
            try:
                result = api.cancel_number(int(oid))
                if _is_cancel_success(result):
                    orders[oid]["status"] = "cancelled"
                    count += 1
                else:
                    err_msg = (result.get("error") or result.get("message") or str(result)) if isinstance(result, dict) else str(result)
                    failed_nums.append(f"+{o.get('number')} ({err_msg[:40]})")
            except Exception as e:
                failed_nums.append(f"+{o.get('number')} ({str(e)[:40]})")
    update_user(uid, orders=orders)

    # Get last bulk session
    u2 = get_user(uid)
    s  = u2.get("last_bulk_session", {})

    if not s or not s.get("provider"):
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("🏠 Menu", callback_data="back_main"))
        
        status_text = f"✅ {count} number(s) cancelled.\n\n"
        if failed_nums:
            failed_list = ", ".join(failed_nums)
            status_text += f"⚠️ <b>Fail ({len(failed_nums)}):</b> {failed_list}\n\n"
        status_text += "❌ Could not re-order: session expired."
        
        try:
            bot.edit_message_text(
                status_text,
                uid, msg.message_id, reply_markup=kb,
            )
        except Exception:
            pass
        return

    reorder_text = f"🔄 <b>Cancelled {count} number(s).</b>\n\n"
    if failed_nums:
        failed_list = ", ".join(failed_nums)
        reorder_text += f"⚠️ <b>Fail ({len(failed_nums)}):</b> {failed_list}\n\n"
    reorder_text += f"⏳ Re-ordering {s.get('quantity', 1)} number(s)..."

    try:
        bot.edit_message_text(
            reorder_text,
            uid, msg.message_id,
        )
    except Exception:
        pass

    # Re-buy with same session
    _do_bulk_order(uid, s)

# ─────────────── Auto OTP Poll ───────────────
def _auto_poll_otp(uid: int, order_id: str, max_attempts: int = 57, interval: int = 20):
    api = get_api(uid)
    if not api:
        return
    for _ in range(max_attempts):
        time.sleep(interval)
        u      = get_user(uid)
        orders = u.get("orders", {})
        if order_id not in orders:
            break
        if orders[order_id].get("status") in ("completed", "cancelled"):
            break

        otp_code = None
        full_text = ""
        try:
            result = api.number_status(int(order_id))
            if isinstance(result, dict):
                if "sms_code" in result:
                    otp_code = result["sms_code"]
                elif "STATUS_OK:" in str(result.get("status", "")):
                    otp_code = str(result["status"]).split("STATUS_OK:")[1]
                elif "sms" in result:
                    otp_code = result["sms"]
                elif "code" in result:
                    otp_code = result["code"]
                full_text = result.get("full_text", "")
            elif isinstance(result, str):
                if "STATUS_OK:" in result:
                    otp_code = result.split("STATUS_OK:")[1]
        except Exception as e:
            print(f"Error checking OTP status for order {order_id}: {str(e)}")
            continue

        if otp_code:
            orders[order_id]["otp"]    = otp_code
            orders[order_id]["status"] = "completed"
            update_user(uid, orders=orders)
            number    = orders[order_id].get("number", "?")
            service   = orders[order_id].get("service", "?")

            otp_text = (
                f"🎉 <b>OTP Received!</b>\n\n"
                f"📞 Number: <code>{number}</code>\n"
                f"🔑 OTP: <code>{otp_code}</code>"
                + (f"\n📩 SMS: {full_text}" if full_text else "")
            )
            kb_done = types.InlineKeyboardMarkup(row_width=2)
            kb_done.add(
                types.InlineKeyboardButton("🔄 Buy Again", callback_data="buy_again_last"),
                types.InlineKeyboardButton("🏠 Menu",      callback_data="back_main"),
            )

            # ✅ Edit the existing order message — no new message in chat
            mid = get_order_msg_id(uid, order_id)
            edited = False
            if mid:
                try:
                    bot.edit_message_text(otp_text, uid, mid, reply_markup=kb_done)
                    edited = True
                except Exception:
                    pass
            if not edited:
                # Fallback: send new message only if edit failed
                bot.send_message(uid, otp_text, reply_markup=kb_done)
                
            # No extra bubble messages sent in chat

            # 📨 Forward to group
            _forward_otp_to_group(uid, order_id, number, otp_code, service, full_text)
            return

    u      = get_user(uid)
    orders = u.get("orders", {})
    if order_id in orders and orders[order_id].get("status") == "active":
        # Automatically cancel the number via the API
        try:
            api.cancel_number(int(order_id))
        except Exception:
            pass
        
        orders[order_id]["status"] = "cancelled"
        update_user(uid, orders=orders)

        timeout_text = (
            f"❌ <b>Order #{order_id} automatically cancelled!</b>\n\n"
            f"No OTP received within 19 minutes.\n"
            f"📞 Number: <code>{orders[order_id].get('number', '?')}</code>"
        )
        kb_to = types.InlineKeyboardMarkup(row_width=2)
        kb_to.add(
            types.InlineKeyboardButton("🔄 Buy Again", callback_data="buy_again_last"),
            types.InlineKeyboardButton("🏠 Menu",      callback_data="back_main"),
        )
        # ✅ Edit the existing order message on timeout too
        mid = get_order_msg_id(uid, order_id)
        edited = False
        if mid:
            try:
                bot.edit_message_text(timeout_text, uid, mid, reply_markup=kb_to)
                edited = True
            except Exception:
                pass
        if not edited:
            bot.send_message(uid, timeout_text, reply_markup=kb_to)

# ─────────────── Manual OTP Check ───────────────
def _check_otp(uid: int, msg: types.Message, order_id: str):
    api    = get_api(uid)
    u      = get_user(uid)
    orders = u.get("orders", {})
    if order_id not in orders:
        return

    # ✅ Always keep tracking this message for auto-poll edits
    track_order_msg(uid, order_id, msg.message_id)

    otp_code = None
    full_text = ""
    try:
        result = api.number_status(int(order_id))
        if isinstance(result, dict):
            if "sms_code" in result:
                otp_code = result["sms_code"]
            elif "STATUS_OK:" in str(result.get("status", "")):
                otp_code = str(result["status"]).split("STATUS_OK:")[1]
            elif "sms" in result:
                otp_code = result["sms"]
            elif "code" in result:
                otp_code = result["code"]
            full_text = result.get("full_text", "")
        elif isinstance(result, str):
            if "STATUS_OK:" in result:
                otp_code = result.split("STATUS_OK:")[1]
    except Exception as e:
        print(f"Error manually checking OTP status for order {order_id}: {str(e)}")

    number = orders[order_id].get("number", "?")

    if otp_code:
        orders[order_id]["otp"]    = otp_code
        orders[order_id]["status"] = "completed"
        update_user(uid, orders=orders)
        service   = orders[order_id].get("service", "?")
        
        text = (
            f"🎉 <b>OTP Received!</b>\n\n"
            f"📞 Number: <code>{number}</code>\n"
            f"🔑 <b>OTP: <code>{otp_code}</code></b>\n"
            + (f"📩 Message: {full_text}" if full_text else "")
        )
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            types.InlineKeyboardButton("🔄 Buy Again", callback_data="buy_again_last"),
            types.InlineKeyboardButton("🏠 Menu",      callback_data="back_main"),
        )
        
        # 📨 Forward to group
        _forward_otp_to_group(uid, order_id, number, otp_code, service, full_text)
        try:
            bot.edit_message_text(text, uid, msg.message_id, reply_markup=kb)
        except Exception:
            pass
        
        return
    else:
        text = (
            f"⏳ <b>OTP not received yet</b>\n\n"
            f"📞 Number: <code>{number}</code>\n"
            f"📊 Status: {result.get('status','pending')}\n\n"
            f"Please check again in a moment."
        )
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            types.InlineKeyboardButton("🔍 Check Again", callback_data=f"check_{order_id}"),
            types.InlineKeyboardButton("🔄 Resend OTP",  callback_data=f"resend_{order_id}"),
        )
        kb.add(
            types.InlineKeyboardButton("❌ Cancel",  callback_data=f"cancel_{order_id}"),
            types.InlineKeyboardButton("⬅️ Back",    callback_data="my_orders"),
        )

    try:
        bot.edit_message_text(text, uid, msg.message_id, reply_markup=kb)
    except Exception:
        pass  # ignore "message not modified" if content unchanged

def _is_cancel_success(result: dict) -> bool:
    if not isinstance(result, dict):
        return False
    err_msg = (
        result.get("message") or result.get("error") or
        result.get("detail") or result.get("msg") or ""
    )
    status_val = str(result.get("status", "")).lower()
    cancel_keywords = ("cancelled", "cancel", "refunded", "refund", "balance refunded")
    msg_lower = err_msg.lower()
    msg_is_cancel_success = any(kw in msg_lower for kw in cancel_keywords)
    return (
        status_val in ("cancelled", "ok", "success", "1", "true")
        or result.get("success") is True
        or msg_is_cancel_success
        or (not err_msg and "error" not in result and "message" not in result)
    )

# ─────────────── Cancel ───────────────
def _cancel_order(uid: int, msg: types.Message, order_id: str):
    api    = get_api(uid)
    result = api.cancel_number(int(order_id))
    is_success = _is_cancel_success(result)

    err_msg = (
        result.get("message") or result.get("error") or
        result.get("detail") or result.get("msg") or ""
    )

    u      = get_user(uid)
    orders = u.get("orders", {})

    if is_success:
        # ✅ Cancelled — update status and show confirmation
        if order_id in orders:
            orders[order_id]["status"] = "cancelled"
            update_user(uid, orders=orders)
        kb = types.InlineKeyboardMarkup(row_width=2)
        # ✅ Show Buy Again only if last_buy_session is fully complete
        u2 = get_user(uid)
        last_s = u2.get("last_buy_session") or {}
        if last_s.get("provider") and last_s.get("service") and last_s.get("country"):
            kb.add(
                types.InlineKeyboardButton("🔄 Buy Again", callback_data="buy_again_last"),
                types.InlineKeyboardButton("📱 New Buy",   callback_data="buy_menu"),
            )
        else:
            kb.add(types.InlineKeyboardButton("📱 Buy Number", callback_data="buy_menu"))
        kb.add(
            types.InlineKeyboardButton("📋 My Orders", callback_data="my_orders"),
            types.InlineKeyboardButton("🏠 Menu",      callback_data="back_main"),
        )
        bot.edit_message_text(
            f"✅ <b>Order #{order_id} cancelled.</b>\n\n"
            f"🔄 Press <b>Buy Again</b> to reorder with same settings.",
            uid, msg.message_id, reply_markup=kb,
        )
    else:
        # ❌ Cancel failed — keep number active, show error as new message below
        number = orders.get(order_id, {}).get("number", order_id)
        num_display = f"+{number}" if not str(number).startswith("+") else number
        bot.send_message(
            uid,
            f"⚠️ <b>Cancel failed for {num_display}</b>\n\n"
            f"<code>{err_msg or str(result)}</code>",
        )

# ─────────────── Resend ───────────────
def _resend_otp(uid: int, msg: types.Message, order_id: str):
    api = get_api(uid)
    api.resend_sms(int(order_id))
    # ✅ Track this message for auto-poll edits after resend
    track_order_msg(uid, order_id, msg.message_id)
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("🔍 Check OTP", callback_data=f"check_{order_id}"),
        types.InlineKeyboardButton("❌ Cancel",     callback_data=f"cancel_{order_id}"),
    )
    bot.edit_message_text(
        f"🔄 <b>OTP resend requested.</b>\n\nOrder: #{order_id}\nPlease check in a moment.",
        uid, msg.message_id, reply_markup=kb,
    )

# ─────────────── My Orders ───────────────
def _show_my_orders(uid: int, msg: types.Message):
    u      = get_user(uid)
    orders = u.get("orders", {})
    active = {oid: o for oid, o in orders.items() if o.get("status") == "active"}

    if not active:
        kb = types.InlineKeyboardMarkup()
        kb.add(
            types.InlineKeyboardButton("📱 Buy Number", callback_data="buy_menu"),
            types.InlineKeyboardButton("🏠 Menu",       callback_data="back_main"),
        )
        bot.edit_message_text("📋 <b>No active orders.</b>\nBuy a number to get started.", uid, msg.message_id, reply_markup=kb)
        return

    kb = types.InlineKeyboardMarkup(row_width=1)
    for oid, o in list(active.items())[-10:]:
        kb.add(types.InlineKeyboardButton(
            f"📞 {o.get('number','?')} | {o.get('service','?')}",
            callback_data=f"order_{oid}"
        ))
    kb.add(types.InlineKeyboardButton("⬅️ Back", callback_data="back_main"))
    bot.edit_message_text(f"📋 <b>Active Orders</b> ({len(active)}):", uid, msg.message_id, reply_markup=kb)

def _show_my_orders_msg(uid: int):
    u      = get_user(uid)
    orders = u.get("orders", {})
    active = {oid: o for oid, o in orders.items() if o.get("status") == "active"}
    if not active:
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(
            types.InlineKeyboardButton("📱 Buy Number", callback_data="buy_menu"),
            types.InlineKeyboardButton("🏠 Menu",       callback_data="back_main"),
        )
        upsend(uid, "📋 <b>No active orders.</b>\nBuy a number to get started.", reply_markup=kb)
        return
    kb = types.InlineKeyboardMarkup(row_width=1)
    for oid, o in list(active.items())[-10:]:
        kb.add(types.InlineKeyboardButton(
            f"📞 {o.get('number','?')} | {o.get('service','?')}",
            callback_data=f"order_{oid}"
        ))
    kb.add(types.InlineKeyboardButton("⬅️ Back", callback_data="back_main"))
    upsend(uid, f"📋 <b>Active Orders</b> ({len(active)}):", reply_markup=kb)

# ─────────────── Order Detail ───────────────
def _show_order_detail(uid: int, msg: types.Message, order_id: str):
    u      = get_user(uid)
    orders = u.get("orders", {})
    o      = orders.get(order_id)
    if not o:
        bot.edit_message_text("❌ Order not found.", uid, msg.message_id)
        return

    text = (
        f"📋 <b>Order Details</b>\n\n"
        f"🆔 ID: <code>{order_id}</code>\n"
        f"📞 Number: <code>{o.get('number','?')}</code>\n"
        f"📱 Service: {o.get('service','?')}\n"
        f"💰 Price: ${o.get('price','?')}\n"
        f"📊 Status: {o.get('status','?')}\n"
        f"🕒 Time: {o.get('created_at','?')[:16]}\n"
    )
    if o.get("otp"):
        text += f"\n🔑 <b>OTP: <code>{o['otp']}</code></b>"

    kb = types.InlineKeyboardMarkup(row_width=2)
    if o.get("status") == "active":
        kb.add(
            types.InlineKeyboardButton("🔍 Check OTP", callback_data=f"check_{order_id}"),
            types.InlineKeyboardButton("🔄 Resend",    callback_data=f"resend_{order_id}"),
        )
        kb.add(types.InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{order_id}"))
    kb.add(types.InlineKeyboardButton("⬅️ Back", callback_data="my_orders"))
    bot.edit_message_text(text, uid, msg.message_id, reply_markup=kb)

# ─────────────── History ───────────────
def _show_history(uid: int, msg: types.Message):
    u          = get_user(uid)
    orders     = u.get("orders", {})
    all_orders = list(orders.items())[-15:]

    if not all_orders:
        bot.edit_message_text("📜 No history yet.", uid, msg.message_id, reply_markup=back_keyboard("main"))
        return

    lines = ["📜 <b>Recent Order History</b>\n"]
    for oid, o in reversed(all_orders):
        icon    = {"active": "🟡", "completed": "✅", "cancelled": "❌"}.get(o.get("status"), "❓")
        otp_str = f" | OTP: <code>{o['otp']}</code>" if o.get("otp") else ""
        lines.append(f"{icon} <code>{o.get('number','?')}</code> ({o.get('service','?')}){otp_str}")

    bot.edit_message_text("\n".join(lines), uid, msg.message_id, reply_markup=back_keyboard("main"))

def _show_history_msg(uid: int):
    u      = get_user(uid)
    orders = u.get("orders", {})
    if not orders:
        upsend(uid, "📜 No history yet.", reply_markup=back_keyboard("main"))
        return
    lines = ["📜 <b>Recent Order History</b>\n"]
    for oid, o in reversed(list(orders.items())[-15:]):
        icon    = {"active": "🟡", "completed": "✅", "cancelled": "❌"}.get(o.get("status"), "❓")
        otp_str = f" | OTP: <code>{o['otp']}</code>" if o.get("otp") else ""
        lines.append(f"{icon} <code>{o.get('number','?')}</code> ({o.get('service','?')}){otp_str}")
    upsend(uid, "\n".join(lines), reply_markup=back_keyboard("main"))

# ─────────────── Admin Panel ───────────────
def _show_admin(uid: int, msg: types.Message):
    users        = load_users()
    total        = len(users)
    active_users = sum(1 for u in users.values() if u.get("api_key"))
    total_orders = sum(len(u.get("orders", {})) for u in users.values())

    grp_status = (
        f"📨 Forward Group: <code>{FORWARD_GROUP_ID}</code>"
        if FORWARD_GROUP_ID
        else "🔕 OTP Forwarding: বন্ধ (group set নেই)"
    )

    text = (
        f"👑 <b>Admin Panel</b>\n\n"
        f"👥 Total Users: <b>{total}</b>\n"
        f"🔑 Logged In: <b>{active_users}</b>\n"
        f"📋 Total Orders: <b>{total_orders}</b>\n"
        f"🕒 Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
        f"{grp_status}"
    )
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("⚙️ Setup Checker", callback_data="setup_checker"),
        types.InlineKeyboardButton("🗑️ Remove Checker", callback_data="remove_checker"),
    )
    if FORWARD_GROUP_ID:
        kb.add(types.InlineKeyboardButton("❌ Remove Forward Group", callback_data="admin_unset_group"))
    kb.add(types.InlineKeyboardButton("⬅️ Back", callback_data="back_main"))
    bot.edit_message_text(text, uid, msg.message_id, reply_markup=kb)

# ─────────────── Start Bot ───────────────
register_commands()
print("[*] smsotps Bot starting...")
print(f"[*] Token: {BOT_TOKEN[:15]}...")
print(f"[*] Admin ID: {ADMIN_ID}")
print("[*] Ready! Waiting for messages...")

while True:
    try:
        bot.infinity_polling(timeout=30, long_polling_timeout=25, skip_pending=True)
    except Exception as e:
        print(f"[!] Connection error: {e}")
        print("[*] Reconnecting in 5 seconds...")
        time.sleep(5)
