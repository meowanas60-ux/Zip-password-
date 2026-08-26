import os
import logging
import subprocess
import asyncio
import gc
from pathlib import Path
from dotenv import load_dotenv
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.error import TelegramError
from database import (
    init_db, add_user, is_vip, give_vip, remove_vip, 
    get_quota, can_recover, increment_quota, list_all_users, user_exists
)

# লোকাল ডেভ এর জন্য .env লোড করুন (Render এ ignoreড)
load_dotenv()

# Render Environment Variables থেকে পড়ুন
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
CHANNEL_1 = os.getenv("CHANNEL_1")
CHANNEL_2 = os.getenv("CHANNEL_2")

# ভ্যালিডেশন
if not TOKEN or ADMIN_ID == 0 or not CHANNEL_1 or not CHANNEL_2:
    raise ValueError("❌ Environment variables missing! Render এ সেট করুন।")

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ডিরেক্টরি তৈরি করুন
UPLOAD_DIR = "uploaded_zips"
Path(UPLOAD_DIR).mkdir(exist_ok=True)

# ডাটাবেস ইনিশিয়ালাইজ করুন
init_db()

# Semaphore (RAM safe)
RECOVERY_SEMAPHORE = asyncio.Semaphore(2)

# ========== HELPER FUNCTIONS ==========

async def is_member_of_channels(app, user_id):
    """চ্যানেল মেম্বার চেক"""
    try:
        member_1 = await app.bot.get_chat_member(CHANNEL_1, user_id)
        status_1 = member_1.status in ['member', 'administrator', 'creator']
        
        member_2 = await app.bot.get_chat_member(CHANNEL_2, user_id)
        status_2 = member_2.status in ['member', 'administrator', 'creator']
        
        return status_1 and status_2
    except TelegramError:
        return False

async def force_join_keyboard():
    """Force join কীবোর্ড"""
    keyboard = [
        [InlineKeyboardButton("📢 চ্যানেল ১", url=f"https://t.me/{CHANNEL_1[1:]}"),
         InlineKeyboardButton("📢 চ্যানেল २", url=f"https://t.me/{CHANNEL_2[1:]}")],
        [InlineKeyboardButton("✅ যাচাই করুন", callback_data="verify_membership")]
    ]
    return InlineKeyboardMarkup(keyboard)

def test_zip_password(zip_file_path, password):
    """পাসওয়ার্ড টেস্ট"""
    try:
        cmd = subprocess.run(
            ['unzip', '-t', '-P', password, zip_file_path],
            capture_output=True,
            text=True,
            timeout=10
        )
        return cmd.returncode == 0
    except:
        return False

def recover_zip_password(zip_file_path):
    """ZIP পাসওয়ার্ড রিকভার"""
    try:
        logger.info(f"🔍 রিকভারি শুরু: {zip_file_path}")
        
        common_passwords = [
            "123456", "password", "123456789", "12345678", "12345",
            "1234567", "password123", "123123", "1234567890",
            "000000", "111111", "admin", "admin123", "root",
            "toor", "pass", "test", "guest", "info", "master",
            "welcome", "letmein", "qwerty", "abc123", "123abc"
        ]
        
        # সাধারণ পাসওয়ার্ড চেষ্টা
        for pwd in common_passwords:
            if test_zip_password(zip_file_path, pwd):
                logger.info(f"✅ পাওয়া: {pwd}")
                return pwd
        
        # john ব্যবহার করুন
        try:
            zip2john_cmd = subprocess.run(
                ['zip2john', zip_file_path],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if zip2john_cmd.returncode == 0:
                hash_data = zip2john_cmd.stdout
                
                john_cmd = subprocess.run(
                    ['john', '--wordlist=/usr/share/wordlists/rockyou.txt', 
                     '--format=PKZIP', '--show'],
                    input=hash_data,
                    capture_output=True,
                    text=True,
                    timeout=300
                )
                
                for line in john_cmd.stdout.split('\n'):
                    if ':' in line:
                        parts = line.split(':')
                        if len(parts) >= 2:
                            password = parts[1].strip()
                            if password and test_zip_password(zip_file_path, password):
                                logger.info(f"✅ পাওয়া: {password}")
                                return password
        except FileNotFoundError:
            logger.warning("⚠️ John না পাওয়া - Render এ ইনস্টল করুন")
        except subprocess.TimeoutExpired:
            logger.error("⏱️ টাইমআউট")
        
        return None
        
    except Exception as e:
        logger.error(f"❌ Error: {e}")
        return None

def cleanup_file(file_path):
    """ফাইল ডিলিট করুন"""
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            gc.collect()
            logger.info(f"🗑️ ডিলিট: {file_path}")
    except Exception as e:
        logger.error(f"Cleanup Error: {e}")

# ========== COMMAND HANDLERS ==========

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """স্টার্ট"""
    user = update.effective_user
    user_id = user.id
    
    if not user_exists(user_id):
        add_user(user_id, user.username or "N/A", user.first_name or "User")
    
    is_member = await is_member_of_channels(context.application, user_id)
    
    if not is_member:
        await update.message.reply_text(
            f"🔒 **চ্যানেলে যোগ দিন!**\n\n"
            f"🔗 {CHANNEL_1}\n"
            f"🔗 {CHANNEL_2}\n\n"
            f"তারপর ✅ যাচাই করুন",
            reply_markup=await force_join_keyboard(),
            parse_mode="Markdown"
        )
        return
    
    vip_status = "🌟 VIP (3/দিন)" if is_vip(user_id) else "📌 Free (1/দিন)"
    
    await update.message.reply_text(
        f"🔓 আলাই {user.first_name}! 👋\n\n"
        f"📊 স্ট্যাটাস: {vip_status}\n\n"
        f"💡 কমান্ড:\n"
        f"/recover - শুরু করুন\n"
        f"/help - সাহায্য\n"
        f"/status - স্ট্যাটাস",
        parse_mode="Markdown"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """সাহায্য"""
    await update.message.reply_text(
        "📖 **কীভাবে ব্যবহার করব:**\n\n"
        "1️⃣ /recover কমান্ড দিন\n"
        "2️⃣ ZIP ফাইল পাঠান\n"
        "3️⃣ অপেক্ষা করুন (1-5 মিনিট)\n"
        "4️⃣ পাসওয়ার্ড পান ✅\n\n"
        "**সীমা:**\n"
        "📌 Free: 1/দিন\n"
        "🌟 VIP: 3/দিন",
        parse_mode="Markdown"
    )

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """স্ট্যাটাস"""
    user_id = update.effective_user.id
    vip_status = "🌟 VIP" if is_vip(user_id) else "📌 Free"
    quota = get_quota(user_id)
    limit = 3 if is_vip(user_id) else 1
    
    await update.message.reply_text(
        f"📊 **আপনার স্ট্যাটাস:**\n\n"
        f"স্ট্যাটাস: {vip_status}\n"
        f"আজ ব্যবহার: {quota}/{limit}\n"
        f"বাকি: {limit - quota}\n\n"
        f"Admin: /start এ ক্লিক করুন",
        parse_mode="Markdown"
    )

async def recover_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """রিকভার শুরু"""
    user_id = update.effective_user.id
    
    is_member = await is_member_of_channels(context.application, user_id)
    if not is_member:
        await update.message.reply_text("🔒 প্রথমে চ্যানেলে যোগ দিন!")
        return
    
    if not can_recover(user_id):
        limit = 3 if is_vip(user_id) else 1
        await update.message.reply_text(
            f"❌ আজকের সীমা শেষ! ({limit}/দিন)\n\n"
            f"24 ঘণ্টা পর আবার চেষ্টা করুন অথবা VIP হন।",
            parse_mode="Markdown"
        )
        return
    
    context.user_data['waiting_for_zip'] = True
    await update.message.reply_text(
        "📂 এখন ZIP ফাইল পাঠান (Max 50MB)\n\n"
        "⏳ রিকভারি 1-5 মিনিট লাগতে পারে..."
    )

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ডকুমেন্ট হ্যান্ডেল করুন"""
    user_id = update.effective_user.id
    
    if not context.user_data.get('waiting_for_zip'):
        return
    
    document = update.message.document
    
    if not document.file_name.endswith('.zip'):
        await update.message.reply_text("❌ শুধুমাত্র ZIP ফাইল আপলোড করুন!")
        return
    
    if document.file_size > 50 * 1024 * 1024:
        await update.message.reply_text("❌ ফাইল 50MB এর বেশি!")
        return
    
    async with RECOVERY_SEMAPHORE:
        try:
            await update.message.reply_text("⏳ ডাউনলোড করছি...")
            
            file = await context.bot.get_file(document.file_id)
            file_path = os.path.join(UPLOAD_DIR, document.file_name)
            await file.download_to_drive(file_path)
            
            await update.message.reply_text("🔍 রিকভারি চলছে...")
            
            password = recover_zip_password(file_path)
            
            if password:
                increment_quota(user_id, document.file_name, password)
                await update.message.reply_text(
                    f"✅ **পাসওয়ার্ড পাওয়া!**\n\n"
                    f"🔐 `{password}`",
                    parse_mode="Markdown"
                )
            else:
                await update.message.reply_text(
                    "❌ পাসওয়ার্ড পাওয়া যায়নি।\n\n"
                    "সম্ভাবনা:\n"
                    "• খুব কমপ্লেক্স পাসওয়ার্ড\n"
                    "• ফাইল কারাপ্ট"
                )
            
            cleanup_file(file_path)
            
        except Exception as e:
            logger.error(f"Error: {e}")
            await update.message.reply_text(f"❌ Error: {str(e)}")
            if os.path.exists(file_path):
                cleanup
