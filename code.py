# bot.py

import os
import logging
import sqlite3
import requests
from datetime import datetime, timedelta

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ForceReply,
    constants
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)

# --- Configuration ---
# Load from environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "5868863582"))
SMM_API_KEY = os.getenv("SMM_API_KEY")
SMM_API_URL = os.getenv("SMM_API_URL")
CHANNEL_1 = os.getenv("CHANNEL_1")
CHANNEL_2 = os.getenv("CHANNEL_2")
PAYMENT_CHANNEL = os.getenv("PAYMENT_CHANNEL")
UPI_ID = os.getenv("UPI_ID")
MARKUP_PERCENT = int(os.getenv("MARKUP_PERCENT", "20"))
REFERRAL_PERCENT = int(os.getenv("REFERRAL_PERCENT", "10"))
BONUS_ENABLED = os.getenv("BONUS_ENABLED", "True").lower() == "true"
REDEEM_ENABLED = os.getenv("REDEEM_ENABLED", "True").lower() == "true"
DAILY_BONUS_AMOUNT = 10 # Example bonus amount

# --- Logging Setup ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Database Setup ---
DB_FILE = "smm_bot.db"

def setup_database():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    # Users table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        balance REAL DEFAULT 0.0,
        referred_by INTEGER,
        join_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_bonus_claim TIMESTAMP
    )
    """)
    # Orders table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS orders (
        order_id INTEGER PRIMARY KEY,
        user_id INTEGER,
        service_id INTEGER,
        link TEXT,
        quantity INTEGER,
        charge REAL,
        status TEXT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (user_id)
    )
    """)
    # Deposits table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS deposits (
        deposit_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        amount REAL,
        status TEXT DEFAULT 'pending', -- pending, approved, rejected
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        message_id INTEGER,
        chat_id INTEGER
    )
    """)
    conn.commit()
    conn.close()

# --- SMM Panel API Helper ---
def smm_api_call(action, params=None):
    if params is None:
        params = {}
    payload = {
        'key': SMM_API_KEY,
        'action': action,
        **params
    }
    try:
        response = requests.post(SMM_API_URL, data=payload, timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"SMM API Error for action '{action}': {e}")
        return None

# --- Database Helper Functions ---
def get_user(user_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    user = cursor.fetchone()
    conn.close()
    if user:
        return {
            "user_id": user[0], "username": user[1], "balance": user[2],
            "referred_by": user[3], "join_date": user[4], "last_bonus_claim": user[5]
        }
    return None

def add_user(user_id, username, referred_by=None):
    if get_user(user_id):
        return
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO users (user_id, username, referred_by) VALUES (?, ?, ?)",
        (user_id, username, referred_by)
    )
    conn.commit()
    conn.close()

def update_balance(user_id, amount):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
    conn.commit()
    conn.close()

def log_order(api_order_id, user_id, service_id, link, quantity, charge, status):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO orders (order_id, user_id, service_id, link, quantity, charge, status)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (api_order_id, user_id, service_id, link, quantity, charge, status))
    conn.commit()
    conn.close()

def get_user_orders(user_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT service_id, quantity, status, order_id FROM orders WHERE user_id = ? ORDER BY timestamp DESC LIMIT 10", (user_id,))
    orders = cursor.fetchall()
    conn.close()
    return orders

def can_claim_bonus(user_id):
    user = get_user(user_id)
    if not user or not user.get("last_bonus_claim"):
        return True, "Ready to claim!"
    
    last_claim_time = datetime.fromisoformat(user["last_bonus_claim"])
    cooldown = timedelta(hours=24)
    if datetime.now() > last_claim_time + cooldown:
        return True, "Ready to claim!"
    else:
        next_claim_time = last_claim_time + cooldown
        remaining = next_claim_time - datetime.now()
        hours, remainder = divmod(remaining.seconds, 3600)
        minutes, _ = divmod(remainder, 60)
        return False, f"{hours}h {minutes}m remaining"

def update_bonus_claim_time(user_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET last_bonus_claim = ? WHERE user_id = ?", (datetime.now().isoformat(), user_id))
    conn.commit()
    conn.close()

# --- Message Deletion Helper ---
async def delete_previous_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    if 'last_message_id' in context.user_data:
        try:
            await context.bot.delete_message(chat_id, context.user_data['last_message_id'])
        except Exception as e:
            logger.warning(f"Could not delete message {context.user_data['last_message_id']}: {e}")
        del context.user_data['last_message_id']

# --- Start & Join Check Flow ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat_id = user.id
    
    # Check if user has joined required channels
    try:
        member1 = await context.bot.get_chat_member(chat_id=CHANNEL_1, user_id=user.id)
        member2 = await context.bot.get_chat_member(chat_id=CHANNEL_2, user_id=user.id)
        if member1.status not in ['member', 'administrator', 'creator'] or \
           member2.status not in ['member', 'administrator', 'creator']:
            await show_join_channels_message(update)
            return
    except Exception as e:
        logger.error(f"Error checking channel membership for {user.id}: {e}")
        await show_join_channels_message(update)
        return

    # User has joined, proceed with registration/main menu
    db_user = get_user(user.id)
    if not db_user:
        referrer_id = None
        if context.args and context.args[0].isdigit():
            potential_referrer_id = int(context.args[0])
            if potential_referrer_id != user.id:
                referrer_id = potential_referrer_id
        
        add_user(user.id, user.username or user.first_name, referrer_id)
        await update.message.reply_text(f"🎉 Welcome, {user.first_name}! You've successfully joined.")

    await main_menu(update, context)

async def show_join_channels_message(update: Update):
    keyboard = [
        [InlineKeyboardButton("🔗 Join Channel 1", url=f"https://t.me/{CHANNEL_1.replace('@', '')}")],
        [InlineKeyboardButton("🔗 Join Channel 2", url=f"https://t.me/{CHANNEL_2.replace('@', '')}")],
        [InlineKeyboardButton("✅ I've Joined", callback_data="check_join")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "⚠️ **Action Required**\n\n"
        "To use this bot, you must be a member of our channels. Please join them and then click the button below.",
        reply_markup=reply_markup,
        parse_mode=constants.ParseMode.MARKDOWN
    )

async def check_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    
    # Re-run the start logic to check membership again
    await start(query, context)


# --- Main Menu ---
async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    keyboard = [
        [InlineKeyboardButton("👤 Account", callback_data="account")],
        [InlineKeyboardButton("💰 Add Funds", callback_data="add_funds")],
        [InlineKeyboardButton("🛒 New Order", callback_data="new_order_category")],
        [InlineKeyboardButton("📦 Track Order", callback_data="track_order")],
        [InlineKeyboardButton("📜 Order History", callback_data="order_history")],
        [InlineKeyboardButton("🎁 Refer & Earn", callback_data="refer_earn")],
    ]
    if BONUS_ENABLED:
        is_ready, _ = can_claim_bonus(user_id)
        bonus_text = "🎲 Daily Bonus" + (" (Ready!)" if is_ready else "")
        keyboard.append([InlineKeyboardButton(bonus_text, callback_data="daily_bonus")])
    if user_id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("👑 Admin Panel", callback_data="admin_panel")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    text = f"👋 **Welcome to the Main Menu, {update.effective_user.first_name}!**\n\nWhat would you like to do today?"

    await delete_previous_message(context, chat_id)
    
    if update.callback_query:
        message = await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode=constants.ParseMode.MARKDOWN)
    else:
        message = await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=constants.ParseMode.MARKDOWN)

    context.user_data['last_message_id'] = message.message_id

async def back_to_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await main_menu(query, context)

# --- Account ---
async def account_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    user = get_user(user_id)
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM orders WHERE user_id = ?", (user_id,))
    total_orders = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM users WHERE referred_by = ?", (user_id,))
    total_referrals = cursor.fetchone()[0]
    conn.close()

    text = (f"👤 **Account Information**\n\n"
            f"**User ID:** `{user['user_id']}`\n"
            f"**Username:** @{user['username']}\n"
            f"**Balance:** `{user['balance']:.2f} coins`\n"
            f"**Total Orders:** `{total_orders}`\n"
            f"**Total Referrals:** `{total_referrals}`")
            
    if user['referred_by']:
        text += f"\n**Referred by:** `{user['referred_by']}`"

    keyboard = [[InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="main_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=constants.ParseMode.MARKDOWN)

# --- Add Funds ---
ADD_FUNDS_AMOUNT, ADD_FUNDS_SCREENSHOT = range(2)

async def add_funds_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = (f"💰 **Add Funds**\n\n"
            f"Please make a payment to the UPI ID below and send the amount you deposited.\n\n"
            f"**UPI ID:** `{UPI_ID}`\n\n"
            f"After payment, please reply with the exact amount you sent (e.g., `100`).")
    keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data="main_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=constants.ParseMode.MARKDOWN)
    return ADD_FUNDS_AMOUNT

async def add_funds_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = float(update.message.text)
        if amount <= 0:
            raise ValueError
        context.user_data['deposit_amount'] = amount
        await update.message.reply_text("✅ Amount received. Now, please send a screenshot of the payment for verification.")
        return ADD_FUNDS_SCREENSHOT
    except (ValueError, TypeError):
        await update.message.reply_text("Invalid amount. Please send a numeric value only (e.g., 100).")
        return ADD_FUNDS_AMOUNT

async def add_funds_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    amount = context.user_data.get('deposit_amount')
    photo_file = await update.message.photo[-1].get_file()

    # Log deposit to DB
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO deposits (user_id, amount, status) VALUES (?, ?, ?)", (user.id, amount, 'pending'))
    deposit_id = cursor.lastrowid
    conn.commit()
    conn.close()

    # Notify admin
    caption = (f"**New Deposit Request**\n\n"
               f"**User:** {user.first_name} (@{user.username})\n"
               f"**User ID:** `{user.id}`\n"
               f"**Amount:** `{amount}`\n"
               f"**Deposit ID:** `{deposit_id}`")
    keyboard = [
        [InlineKeyboardButton(f"✅ Approve {amount}", callback_data=f"approve_deposit_{deposit_id}")],
        [InlineKeyboardButton("❌ Reject", callback_data=f"reject_deposit_{deposit_id}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_photo(chat_id=ADMIN_ID, photo=photo_file.file_id, caption=caption, reply_markup=reply_markup, parse_mode=constants.ParseMode.MARKDOWN)
    
    # Confirm to user
    await update.message.reply_text("✅ Your deposit request has been submitted. You will be notified upon approval.")
    
    # Go back to main menu
    await main_menu(update, context)
    return ConversationHandler.END

async def approve_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Processing approval...")
    
    data = query.data.split('_')
    deposit_id = int(data[2])

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, amount, status FROM deposits WHERE deposit_id = ?", (deposit_id,))
    deposit_info = cursor.fetchone()
    
    if not deposit_info or deposit_info[2] != 'pending':
        await query.edit_message_caption(caption=query.message.caption + "\n\n**Status: Already processed.**", parse_mode=constants.ParseMode.MARKDOWN)
        conn.close()
        return

    user_id, amount, _ = deposit_info
    
    # Update balance and handle referral
    update_balance(user_id, amount)
    
    user_data = get_user(user_id)
    if user_data and user_data['referred_by']:
        referrer_id = user_data['referred_by']
        # Check if this is the first deposit
        cursor.execute("SELECT COUNT(*) FROM deposits WHERE user_id = ? AND status = 'approved'", (user_id,))
        approved_deposits_count = cursor.fetchone()[0]
        
        if approved_deposits_count == 0: # This is the first approved deposit
            referral_bonus = amount * (REFERRAL_PERCENT / 100)
            update_balance(referrer_id, referral_bonus)
            try:
                await context.bot.send_message(
                    chat_id=referrer_id,
                    text=f"🎉 **Referral Bonus!** You've received a bonus of `{referral_bonus:.2f}` coins from your referral's first deposit."
                )
            except Exception as e:
                logger.error(f"Failed to send referral bonus notification to {referrer_id}: {e}")

    # Update deposit status
    cursor.execute("UPDATE deposits SET status = 'approved' WHERE deposit_id = ?", (deposit_id,))
    conn.commit()
    conn.close()

    # Notify user
    try:
        await context.bot.send_message(chat_id=user_id, text=f"✅ Your deposit of `{amount}` has been approved and added to your balance.")
    except Exception as e:
        logger.error(f"Failed to send deposit approval message to {user_id}: {e}")

    # Log to payment channel
    try:
        await context.bot.send_message(
            chat_id=PAYMENT_CHANNEL,
            text=f"✅ **Deposit Approved**\nUser ID: `{user_id}`\nAmount: `{amount}`\nDate: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            parse_mode=constants.ParseMode.MARKDOWN
        )
    except Exception as e:
        logger.error(f"Failed to log payment to channel {PAYMENT_CHANNEL}: {e}")

    # Update admin message
    await query.edit_message_caption(
        caption=query.message.caption + f"\n\n**Status: Approved by admin on {datetime.now().strftime('%Y-%m-%d %H:%M')}**",
        parse_mode=constants.ParseMode.MARKDOWN
    )

async def reject_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Processing rejection...")
    
    deposit_id = int(query.data.split('_')[2])
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE deposits SET status = 'rejected' WHERE deposit_id = ?", (deposit_id,))
    conn.commit()
    
    cursor.execute("SELECT user_id, amount FROM deposits WHERE deposit_id = ?", (deposit_id,))
    user_id, amount = cursor.fetchone()
    conn.close()

    try:
        await context.bot.send_message(chat_id=user_id, text=f"❌ Your deposit request for `{amount}` has been rejected. Please contact support if you believe this is an error.")
    except Exception as e:
        logger.error(f"Failed to send deposit rejection message to {user_id}: {e}")

    await query.edit_message_caption(
        caption=query.message.caption + f"\n\n**Status: Rejected by admin on {datetime.now().strftime('%Y-%m-%d %H:%M')}**",
        parse_mode=constants.ParseMode.MARKDOWN
    )

# --- New Order Conversation ---
(SELECTING_CATEGORY, SELECTING_SERVICE, ENTERING_LINK,
 ENTERING_QUANTITY, CONFIRMING_ORDER) = range(5)

async def new_order_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    services = smm_api_call('services')
    if not services:
        await query.edit_message_text("❌ Could not fetch services from the provider. Please try again later.",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="main_menu")]]))
        return ConversationHandler.END

    categories = sorted(list(set(s['category'] for s in services)))
    context.user_data['smm_services'] = services
    
    keyboard = [[InlineKeyboardButton(cat, callback_data=f"cat_{cat}")] for cat in categories]
    keyboard.append([InlineKeyboardButton("⬅️ Cancel", callback_data="cancel_order")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text("🛒 **Step 1: Choose a Category**", reply_markup=reply_markup)
    return SELECTING_SERVICE

async def new_order_service(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    category = query.data.split('_', 1)[1]
    context.user_data['category'] = category

    services = context.user_data['smm_services']
    category_services = sorted([s for s in services if s['category'] == category], key=lambda x: x['name'])
    
    keyboard = []
    for s in category_services:
        price = float(s['rate']) * (1 + MARKUP_PERCENT / 100)
        keyboard.append([InlineKeyboardButton(f"{s['name']} - ${price:.4f}/1k", callback_data=f"svc_{s['service']}")])
    
    keyboard.append([InlineKeyboardButton("⬅️ Back to Categories", callback_data="new_order_category")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(f"🛒 **Step 2: Choose a Service in '{category}'**", reply_markup=reply_markup)
    return ENTERING_LINK

async def new_order_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    service_id = int(query.data.split('_', 1)[1])
    
    services = context.user_data['smm_services']
    service = next((s for s in services if int(s['service']) == service_id), None)
    if not service:
        await query.edit_message_text("Error: Service not found. Please start over.", 
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="main_menu")]]))
        return ConversationHandler.END

    context.user_data['service'] = service
    await query.edit_message_text(f"🛒 **Step 3: Enter the Link**\n\n**Service:** {service['name']}\n\nPlease reply with the link for your order.")
    return ENTERING_QUANTITY
    
async def new_order_quantity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    link = update.message.text
    context.user_data['link'] = link
    service = context.user_data['service']

    await update.message.reply_text(f"🛒 **Step 4: Enter Quantity**\n\n**Min:** {service['min']}\n**Max:** {service['max']}\n\nPlease reply with the desired quantity.")
    return CONFIRMING_ORDER

async def new_order_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        quantity = int(update.message.text)
        service = context.user_data['service']
        min_q, max_q = int(service['min']), int(service['max'])
        if not (min_q <= quantity <= max_q):
            await update.message.reply_text(f"❌ Quantity must be between {min_q} and {max_q}. Please try again.")
            return ENTERING_QUANTITY
    except ValueError:
        await update.message.reply_text("❌ Invalid quantity. Please enter a whole number.")
        return ENTERING_QUANTITY

    context.user_data['quantity'] = quantity
    user_id = update.effective_user.id
    user_balance = get_user(user_id)['balance']

    rate = float(service['rate'])
    charge = (quantity / 1000) * rate * (1 + MARKUP_PERCENT / 100)
    context.user_data['charge'] = charge

    text = (f"🛒 **Step 5: Confirm Your Order**\n\n"
            f"**Service:** {service['name']}\n"
            f"**Link:** `{context.user_data['link']}`\n"
            f"**Quantity:** `{quantity}`\n"
            f"**Total Cost:** `{charge:.4f} coins`\n\n"
            f"Your current balance is `{user_balance:.2f}` coins.")
    
    keyboard = []
    if user_balance >= charge:
        keyboard.append([InlineKeyboardButton("✅ Confirm Order", callback_data="confirm_order_final")])
    else:
        text += "\n\n⚠️ **Insufficient balance!** Please add funds to proceed."
        keyboard.append([InlineKeyboardButton("💰 Add Funds", callback_data="add_funds")])
    
    keyboard.append([InlineKeyboardButton("⬅️ Cancel", callback_data="cancel_order")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=constants.ParseMode.MARKDOWN)
    
    return CONFIRMING_ORDER # Stay in this state to handle the callback

async def new_order_final(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Placing order...")
    
    user_id = query.from_user.id
    user_balance = get_user(user_id)['balance']
    charge = context.user_data['charge']

    if user_balance < charge:
        await query.edit_message_text("❌ Your balance is too low to place this order.",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="main_menu")]]))
        return ConversationHandler.END

    service = context.user_data['service']
    link = context.user_data['link']
    quantity = context.user_data['quantity']

    order_params = {
        'service': service['service'],
        'link': link,
        'quantity': quantity
    }
    api_response = smm_api_call('add', order_params)

    if api_response and 'order' in api_response:
        api_order_id = api_response['order']
        update_balance(user_id, -charge)
        log_order(api_order_id, user_id, service['service'], link, quantity, charge, 'Pending')
        
        text = (f"✅ **Order Placed Successfully!**\n\n"
                f"**Order ID:** `{api_order_id}`\n"
                f"**Service:** {service['name']}\n"
                f"**Charge:** `{charge:.4f}` coins\n\n"
                f"You can track its status using the Track Order button.")
        
        # Log to payment channel
        try:
            await context.bot.send_message(
                chat_id=PAYMENT_CHANNEL,
                text=f"🛒 **New Order Placed**\nUser ID: `{user_id}`\nOrder ID: `{api_order_id}`\nService ID: `{service['service']}`\nCharge: `{charge:.4f}`",
                parse_mode=constants.ParseMode.MARKDOWN
            )
        except Exception as e:
            logger.error(f"Failed to log new order to channel {PAYMENT_CHANNEL}: {e}")

    else:
        error_msg = api_response.get('error', 'Unknown error from SMM provider.')
        text = f"❌ **Order Failed!**\n\n**Reason:** {error_msg}\n\nYour balance has not been charged. Please check your link and try again."

    await query.edit_message_text(text, parse_mode=constants.ParseMode.MARKDOWN,
                                  reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="main_menu")]]))
    return ConversationHandler.END

async def cancel_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Order cancelled.")
    await main_menu(query, context)
    return ConversationHandler.END

# --- Track Order ---
TRACK_ORDER_ID = 0
async def track_order_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("📦 **Track Order**\n\nPlease reply with the Order ID you want to track.",
                                  reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="main_menu")]]))
    return TRACK_ORDER_ID

async def track_order_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    order_id = update.message.text
    if not order_id.isdigit():
        await update.message.reply_text("Invalid Order ID. It should be a number.")
        return TRACK_ORDER_ID

    status_response = smm_api_call('status', {'order': order_id})
    if status_response and 'status' in status_response:
        status = status_response['status']
        charge = status_response.get('charge', 'N/A')
        start_count = status_response.get('start_count', 'N/A')
        remains = status_response.get('remains', 'N/A')

        text = (f"**Order Status for ID:** `{order_id}`\n\n"
                f"**Status:** `{status}`\n"
                f"**Charge:** `{charge}`\n"
                f"**Start Count:** `{start_count}`\n"
                f"**Remains:** `{remains}`")
    else:
        error_msg = status_response.get('error', 'Could not retrieve status or order not found.')
        text = f"❌ **Error:** {error_msg}"
        
    await update.message.reply_text(text, parse_mode=constants.ParseMode.MARKDOWN)
    await main_menu(update, context)
    return ConversationHandler.END

# --- Other Main Menu Functions ---
async def order_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    orders = get_user_orders(user_id)

    if not orders:
        text = "You have no past orders."
    else:
        text = "📜 **Your Last 10 Orders:**\n\n"
        for service_id, quantity, status, order_id in orders:
            text += f"ID: `{order_id}` | Svc: `{service_id}` | Qty: `{quantity}` | Stat: `{status}`\n"
    
    keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data="main_menu")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=constants.ParseMode.MARKDOWN)

async def refer_earn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    bot_username = (await context.bot.get_me()).username
    referral_link = f"https://t.me/{bot_username}?start={user_id}"

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM users WHERE referred_by = ?", (user_id,))
    referral_count = cursor.fetchone()[0]
    conn.close()

    text = (f"🎁 **Refer & Earn**\n\n"
            f"Invite your friends and earn a `{REFERRAL_PERCENT}%` bonus on their first deposit!\n\n"
            f"**Your unique referral link:**\n`{referral_link}`\n\n"
            f"**Total users referred:** `{referral_count}`\n\n"
            f"Share this link and start earning today!")
    
    keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data="main_menu")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=constants.ParseMode.MARKDOWN)

async def daily_bonus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if not BONUS_ENABLED:
        await query.edit_message_text("The daily bonus is currently disabled.", 
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="main_menu")]]))
        return
        
    is_ready, message = can_claim_bonus(user_id)
    if is_ready:
        update_balance(user_id, DAILY_BONUS_AMOUNT)
        update_bonus_claim_time(user_id)
        text = f"🎉 You've claimed your daily bonus of `{DAILY_BONUS_AMOUNT}` coins! Come back in 24 hours."
    else:
        text = f"⚠️ You have already claimed your bonus. Please wait. {message}"

    await query.edit_message_text(text, parse_mode=constants.ParseMode.MARKDOWN,
                                  reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="main_menu")]]))

# --- Admin Panel ---
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        await query.answer("You are not authorized.", show_alert=True)
        return
    await query.answer()

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]
    cursor.execute("SELECT SUM(balance) FROM users")
    total_balance = cursor.fetchone()[0] or 0
    cursor.execute("SELECT COUNT(*) FROM orders")
    total_orders = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM deposits WHERE status = 'pending'")
    pending_deposits = cursor.fetchone()[0]
    conn.close()

    text = (f"👑 **Admin Panel**\n\n"
            f"**Bot Statistics:**\n"
            f"- Total Users: `{total_users}`\n"
            f"- Total Coin Balance: `{total_balance:.2f}`\n"
            f"- Total Orders: `{total_orders}`\n"
            f"- Pending Deposits: `{pending_deposits}`")

    keyboard = [
        [InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")],
        [InlineKeyboardButton("👤 User Management", callback_data="admin_users")],
        [InlineKeyboardButton("💳 View Pending Deposits", callback_data="admin_deposits")],
        [InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="main_menu")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=constants.ParseMode.MARKDOWN)

# Fallback for conversation handlers
async def conv_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Action cancelled or timed out.")
    await main_menu(update, context)
    return ConversationHandler.END


def main() -> None:
    """Run the bot."""
    setup_database()
    
    application = Application.builder().token(BOT_TOKEN).build()
    
    # --- Conversation Handlers ---
    add_funds_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_funds_start, pattern='^add_funds$')],
        states={
            ADD_FUNDS_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_funds_amount)],
            ADD_FUNDS_SCREENSHOT: [MessageHandler(filters.PHOTO, add_funds_screenshot)],
        },
        fallbacks=[CallbackQueryHandler(back_to_main_menu, pattern='^main_menu$'), CommandHandler('start', start)],
        conversation_timeout=300
    )

    new_order_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(new_order_category, pattern='^new_order_category$')],
        states={
            SELECTING_SERVICE: [CallbackQueryHandler(new_order_service, pattern='^cat_')],
            ENTERING_LINK: [CallbackQueryHandler(new_order_link, pattern='^svc_')],
            ENTERING_QUANTITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, new_order_quantity)],
            CONFIRMING_ORDER: [MessageHandler(filters.TEXT & ~filters.COMMAND, new_order_confirm),
                               CallbackQueryHandler(new_order_final, pattern='^confirm_order_final$')]
        },
        fallbacks=[
            CallbackQueryHandler(cancel_order, pattern='^cancel_order$'),
            CallbackQueryHandler(new_order_category, pattern='^new_order_category$'), # Go back to categories
            CommandHandler('start', start)
        ],
        conversation_timeout=600
    )
    
    track_order_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(track_order_start, pattern='^track_order$')],
        states={
            TRACK_ORDER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, track_order_id)]
        },
        fallbacks=[CallbackQueryHandler(back_to_main_menu, pattern='^main_menu$'), CommandHandler('start', start)],
        conversation_timeout=120
    )

    # --- Handlers ---
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(check_join_callback, pattern='^check_join$'))
    application.add_handler(CallbackQueryHandler(back_to_main_menu, pattern='^main_menu$'))
    
    # Main menu buttons
    application.add_handler(CallbackQueryHandler(account_info, pattern='^account$'))
    application.add_handler(CallbackQueryHandler(order_history, pattern='^order_history$'))
    application.add_handler(CallbackQueryHandler(refer_earn, pattern='^refer_earn$'))
    application.add_handler(CallbackQueryHandler(daily_bonus, pattern='^daily_bonus$'))
    
    # Conversation handlers
    application.add_handler(add_funds_handler)
    application.add_handler(new_order_handler)
    application.add_handler(track_order_handler)
    
    # Admin handlers
    application.add_handler(CallbackQueryHandler(admin_panel, pattern='^admin_panel$'))
    application.add_handler(CallbackQueryHandler(approve_deposit, pattern=r'^approve_deposit_'))
    application.add_handler(CallbackQueryHandler(reject_deposit, pattern=r'^reject_deposit_'))
    
    # Run the bot
    logger.info("Bot is starting...")
    application.run_polling()


if __name__ == "__main__":
    main()