import os
import logging
import sqlite3
import asyncio
import subprocess
import gc
import json
import time
from datetime import datetime, timedelta
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Chat
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from telegram.constants import ChatMemberStatus, ParseMode
from telegram.error import TelegramError
import zipfile
from pathlib import Path

load_dotenv()

# ===================== LOGGING =====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ===================== CONFIG =====================
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "7701549179"))
CHANNEL_1 = os.getenv("CHANNEL_1", "@sahatanas")
CHANNEL_2 = os.getenv("CHANNEL_2", "@sahatanass")

MAX_FILE_SIZE = 50 * 1024 * 1024
UPLOAD_DIR = "uploaded_zips"
DB_PATH = "users.db"

Path(UPLOAD_DIR).mkdir(exist_ok=True)

# ===================== SEMAPHORE =====================
processing_semaphore = asyncio.Semaphore(2)

# ===================== DATABASE FUNCTIONS =====================
def init_db():
    """ডাটাবেস ইনিশিয়ালাইজ"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            is_vip INTEGER DEFAULT 0,
            recovery_count INTEGER DEFAULT 0,
            last_recovery TIMESTAMP,
            total_recoveries INTEGER DEFAULT 0,
            blocked INTEGER DEFAULT 0,
            join_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS admin_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id INTEGER,
            action TEXT,
            target_user_id INTEGER,
            details TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS recovery_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            filename TEXT,
            password TEXT,
            status TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()
    logger.info("✅ ডাটাবেস প্রস্তুত")

def register_user(user_id: int, username: str):
    """নতুন ইউজার রেজিস্টার"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        INSERT OR IGNORE INTO users (user_id, username, join_date)
        VALUES (?, ?, ?)
    ''', (user_id, username, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_user_quota(user_id: int) -> dict:
    """ইউজারের কোটা চেক"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute('SELECT is_vip, recovery_count, last_recovery, blocked FROM users WHERE user_id = ?', (user_id,))
    result = c.fetchone()
    conn.close()
    
    if not result:
        return {"can_recover": True, "remaining": 1, "is_vip": False, "blocked": False}
    
    is_vip, count, last_recovery, blocked = result
    
    if blocked:
        return {"can_recover": False, "remaining": 0, "is_vip": False, "blocked": True}
    
    if last_recovery:
        last_time = datetime.fromisoformat(last_recovery)
        if datetime.now() - last_time > timedelta(hours=24):
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute('UPDATE users SET recovery_count = 0 WHERE user_id = ?', (user_id,))
            conn.commit()
            conn.close()
            count = 0
    
    max_quota = 3 if is_vip else 1
    can_recover = count < max_quota
    remaining = max_quota - count
    
    return {"can_recover": can_recover, "remaining": remaining, "is_vip": is_vip, "blocked": False}

def update_recovery_count(user_id: int):
    """Recovery count বাড়ান"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        UPDATE users SET 
        recovery_count = recovery_count + 1, 
        last_recovery = ?,
        total_recoveries = total_recoveries + 1 
        WHERE user_id = ?
    ''', (datetime.now().isoformat(), user_id))
    conn.commit()
    conn.close()

def save_recovery_history(user_id: int, filename: str, password: str, status: str):
    """রিকভারি হিস্টরি সেভ করুন"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        INSERT INTO recovery_history (user_id, filename, password, status)
        VALUES (?, ?, ?, ?)
    ''', (user_id, filename, password, status))
    conn.commit()
    conn.close()

def add_admin_log(admin_id: int, action: str, target_user: int, details: str = ""):
    """Admin লগ সেভ করুন"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        INSERT INTO admin_logs (admin_id, action, target_user_id, details)
        VALUES (?, ?, ?, ?)
    ''', (admin_id, action, target_user, details))
    conn.commit()
    conn.close()

def is_admin(user_id: int) -> bool:
    """অ্যাডমিন চেক করুন"""
    return user_id == ADMIN_ID

def block_user(user_id: int):
    """ইউজার ব্লক করুন"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('UPDATE users SET blocked = 1 WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

def unblock_user(user_id: int):
    """ইউজার আনব্লক করুন"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('UPDATE users SET blocked = 0 WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

def make_vip(user_id: int):
    """VIP বানান"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('UPDATE users SET is_vip = 1 WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

def remove_vip(user_id: int):
    """VIP সরান"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('UPDATE users SET is_vip = 0 WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

def get_all_users():
    """সব ইউজার পান"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT user_id, username, is_vip, total_recoveries, blocked FROM users ORDER BY join_date DESC')
    users = c.fetchall()
    conn.close()
    return users

def get_stats():
    """স্ট্যাটিসটিক্স পান"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute('SELECT COUNT(*) FROM users')
    total_users = c.fetchone()[0]
    
    c.execute('SELECT COUNT(*) FROM users WHERE is_vip = 1')
    vip_users = c.fetchone()[0]
    
    c.execute('SELECT COUNT(*) FROM users WHERE blocked = 1')
    blocked_users = c.fetchone()[0]
    
    c.execute('SELECT SUM(total_recoveries) FROM users')
    total_recovery = c.fetchone()[0] or 0
    
    c.execute('SELECT COUNT(*) FROM recovery_history WHERE status = "success"')
    successful = c.fetchone()[0]
    
    conn.close()
    
    return {
        "total_users": total_users,
        "vip_users": vip_users,
        "blocked_users": blocked_users,
        "total_recovery": total_recovery,
        "successful": successful
    }

# ===================== WORDLIST =====================
def get_wordlists() -> list:
    """সব উপলব্ধ wordlist পান"""
    wordlists = [
        "/usr/share/wordlists/rockyou.txt",
        "/usr/share/wordlists/rockyou-50.txt",
        "/usr/share/wordlists/wordlist.txt",
        "/usr/share/dict/american-english",
        "/usr/share/dict/words",
        "/usr/share/dict/cracklib-small"
    ]
    
    available = []
    for path in wordlists:
        if os.path.exists(path):
            try:
                size_mb = os.path.getsize(path) / 1024 / 1024
                available.append(path)
                logger.info(f"✅ Wordlist: {path.split('/')[-1]} ({size_mb:.1f}MB)")
            except:
                pass
    
    return available

# ===================== PASSWORD RECOVERY =====================
async def recover_zip_password(zip_file: str, user_id: int) -> tuple:
    """ZIP পাসওয়ার্ড রিকভার করুন"""
    try:
        logger.info(f"🔍 Starting recovery: {zip_file} (User: {user_id})")
        
        wordlists = get_wordlists()
        if not wordlists:
            logger.error("❌ কোনো wordlist পাওয়া যায়নি")
            return None, "wordlist_not_found"
        
        # ===================== METHOD 1: zip2john + john =====================
        logger.info(f"📋 Method 1: John The Ripper ({len(wordlists)} wordlists)")
        
        for idx, wordlist in enumerate(wordlists, 1):
            logger.info(f"🔑 Wordlist {idx}/{len(wordlists)}: {wordlist.split('/')[-1]}")
            
            try:
                # Hash বের করুন
                result = subprocess.run(
                    ['zip2john', zip_file],
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                
                if result.returncode != 0:
                    logger.warning(f"⚠️ zip2john ব্যর্থ")
                    continue
                
                hash_data = result.stdout
                
                # John দিয়ে ক্র্যাক করুন
                john_result = subprocess.run(
                    ['john', '--wordlist=' + wordlist, '--format=PKZIP', '--show'],
                    input=hash_data,
                    capture_output=True,
                    text=True,
                    timeout=600
                )
                
                # পাসওয়ার্ড খুঁজুন
                for line in john_result.stdout.split('\n'):
                    if ':' in line:
                        parts = line.split(':')
                        if len(parts) >= 2:
                            password = parts[1].strip()
                            if password and password != '?':
                                logger.info(f"✅ পাওয়া: {password}")
                                return password, "success"
            
            except subprocess.TimeoutExpired:
                logger.warning(f"⏱️ {wordlist} টাইমআউট")
                continue
            except FileNotFoundError:
                logger.error(f"❌ john বা zip2john ইনস্টল নেই")
                return None, "john_not_found"
            except Exception as e:
                logger.warning(f"⚠️ Error: {e}")
                continue
        
        # ===================== METHOD 2: Direct ZIP =====================
        logger.info("📋 Method 2: Direct ZIP Open")
        try:
            with zipfile.ZipFile(zip_file, 'r') as zf:
                zf.testzip()
                logger.info("✅ পাসওয়ার্ড ছাড়াই খোলা যায়")
                return "", "no_password"
        except RuntimeError:
            logger.warning("🔐 ZIP এনক্রিপ্টেড")
        except Exception as e:
            logger.error(f"❌ Error: {e}")
        
        logger.error("❌ পাসওয়ার্ড পাওয়া যায়নি")
        return None, "not_found"
    
    except Exception as e:
        logger.error(f"❌ Fatal Error: {e}")
        return None, "error"
    
    finally:
        gc.collect()

# ===================== TELEGRAM HANDLERS =====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start Command"""
    user = update.effective_user
    user_id = user.id
    username = user.username or "Unknown"
    
    register_user(user_id, username)
    
    # Force join check
    not_joined = []
    for channel in [CHANNEL_1, CHANNEL_2]:
        try:
            member = await context.bot.get_chat_member(channel, user_id)
            if member.status not in [ChatMemberStatus.MEMBER, ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR]:
                not_joined.append(channel)
        except:
            not_joined.append(channel)
    
    if not_joined:
        keyboard = [
            [InlineKeyboardButton("Channel Join করুন", url=f"https://t.me/{ch.replace('@', '')}")] 
            for ch in not_joined
        ]
        keyboard.append([InlineKeyboardButton("✅ Join করেছি", callback_data="check_join")])
        
        await update.message.reply_text(
            f"❌ আগে এই চ্যানেল join করুন:\n\n" + 
            "\n".join([f"🔗 {ch}" for ch in not_joined]),
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    
    keyboard = [
        [InlineKeyboardButton("🔐 Password Recover", callback_data="start_recover")],
        [InlineKeyboardButton("📊 My Status", callback_data="show_status")],
        [InlineKeyboardButton("📖 Help", callback_data="show_help")]
    ]
    
    await update.message.reply_text(
        f"""👋 **স্বাগতম {user.first_name}!**

🔐 **ZIP Password Recovery Bot**

✨ কী করতে পারি:
• ZIP ফাইল আপলোড করুন
• পাসওয়ার্ড খুঁজে দেব

⚡ **Database: 14+ মিলিয়ন পাসওয়ার্ড**
🚀 **Advanced Methods + Brute Force**

💾 Max File: 50MB
⏳ Processing: 2 একসাথে

""",
