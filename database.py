import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

DB_FILE = "users.db"

def init_db():
    """ডাটাবেস ইনিশিয়ালাইজ করুন"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        is_vip INTEGER DEFAULT 0,
        recovery_count INTEGER DEFAULT 0,
        last_reset TEXT,
        joined_at TEXT
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS recovery_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        file_name TEXT,
        password TEXT,
        timestamp TEXT,
        FOREIGN KEY(user_id) REFERENCES users(user_id)
    )''')
    
    conn.commit()
    conn.close()

def add_user(user_id, username, first_name):
    """নতুন ইউজার যোগ করুন"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    c.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    if c.fetchone():
        conn.close()
        return False
    
    c.execute('''INSERT INTO users (user_id, username, first_name, joined_at) 
                 VALUES (?, ?, ?, ?)''',
              (user_id, username, first_name, datetime.now().isoformat()))
    
    conn.commit()
    conn.close()
    return True

def is_vip(user_id):
    """VIP চেক করুন"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT is_vip FROM users WHERE user_id = ?', (user_id,))
    result = c.fetchone()
    conn.close()
    return result and result[0] == 1

def give_vip(user_id):
    """VIP দিন"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('UPDATE users SET is_vip = 1 WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

def remove_vip(user_id):
    """VIP বাতিল করুন"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('UPDATE users SET is_vip = 0 WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

def get_quota(user_id):
    """আজকের কোটা পান"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    c.execute('SELECT recovery_count, last_reset, is_vip FROM users WHERE user_id = ?', (user_id,))
    result = c.fetchone()
    
    if not result:
        conn.close()
        return 0
    
    recovery_count, last_reset, is_vip_val = result
    
    # রিসেট চেক করুন
    if last_reset:
        last_reset_dt = datetime.fromisoformat(last_reset)
        if datetime.now() - last_reset_dt > timedelta(hours=24):
            c.execute('UPDATE users SET recovery_count = 0, last_reset = ? WHERE user_id = ?',
                     (datetime.now().isoformat(), user_id))
            conn.commit()
            conn.close()
            return 0
    
    conn.close()
    return recovery_count

def can_recover(user_id):
    """রিকভারি করতে পারবেন কিনা চেক করুন"""
    is_vip_val = is_vip(user_id)
    limit = 3 if is_vip_val else 1
    current = get_quota(user_id)
    return current < limit

def increment_quota(user_id, file_name, password):
    """কোটা বাড়ান এবং লগ করুন"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    c.execute('''UPDATE users SET recovery_count = recovery_count + 1, last_reset = ? 
                 WHERE user_id = ?''',
              (datetime.now().isoformat(), user_id))
    
    c.execute('''INSERT INTO recovery_logs (user_id, file_name, password, timestamp) 
                 VALUES (?, ?, ?, ?)''',
              (user_id, file_name, password, datetime.now().isoformat()))
    
    conn.commit()
    conn.close()

def list_all_users():
    """সব ইউজার লিস্ট করুন"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT user_id, username, is_vip, recovery_count FROM users ORDER BY joined_at DESC')
    users = c.fetchall()
    conn.close()
    return users

def user_exists(user_id):
    """ইউজার আছে কিনা চেক করুন"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT user_id FROM users WHERE user_id = ?', (user_id,))
    result = c.fetchone()
    conn.close()
    return result is not None
