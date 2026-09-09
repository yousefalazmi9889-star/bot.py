import os
import sqlite3
import threading
from pathlib import Path
from threading import Thread

import discord
from discord.ext import commands
from flask import Flask

# --- 1. سيرفر الويب لتجاوز خمول Render ---
app = Flask("")


@app.route("/")
def home():
    return "Bot is alive and running!"


def run():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)


def keep_alive():
    t = Thread(target=run)
    t.start()


keep_alive()

# --- 2. تهيئة قواعد البيانات التابعة للألعاب ونقاط الإدارة ---
DATABASE_FILE = Path(__file__).resolve().parent / "leon_tickets.db"
ADMIN_DB_FILE = Path(__file__).resolve().parent / "admin_points.db"


class GameDatabase:

    def __init__(self, path: Path) -> None:
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.lock = threading.RLock()

    def initialize(self) -> None:
        with self.lock:
            self.connection.execute(
                """CREATE TABLE IF NOT EXISTS game_questions (
                    id INTEGER PRIMARY KEY,
                    kind TEXT NOT NULL,
                    question TEXT NOT NULL,
                    options TEXT NOT NULL,
                    correct_index INTEGER NOT NULL
                )"""
            )
            self.connection.execute(
                """CREATE TABLE IF NOT EXISTS game_scores (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    game_type TEXT NOT NULL,
                    score INTEGER NOT NULL,
                    total INTEGER NOT NULL,
                    played_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )"""
            )
            self.connection.commit()


game_database = GameDatabase(DATABASE_FILE)
game_database.initialize()

# --- 3. إعدادات وقواعد البوت الرئيسية ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def setup_hook():
    # تحميل موديول نقاط الإدارة كـ Extension
    try:
        await bot.load_extension("admin_points")
        print("✅ Successfully loaded admin_points module.")
    except Exception as e:
        print(f"❌ [Admin Points Load Error]: {e}")


@bot.event
async def on_ready():
    # تسجيل أوامر الألعاب
    try:
        from games import register_game_commands

        register_game_commands(bot)
        print("✅ Registered game commands successfully.")
    except Exception as e:
        print(f"❌ [Games Setup Error]: {e}")

    # مزامنة جميع أوامر الـ Slash الشاملة مع ديسكورد
    try:
        synced = await bot.tree.sync()
        print(f"✅ Successfully synced {len(synced)} command(s).")
    except Exception as e:
        print(f"❌ Failed to sync commands: {e}")

    print(f"🤖 Logged in as {bot.user} ({bot.user.id})")


# --- 4. تشغيل البوت ---
token = os.environ.get("DISCORD_TOKEN")
if not token:
    print("❌ ERROR: DISCORD_TOKEN variable is missing from Environment Variables!")
else:
    bot.run(token)
