import os
import sqlite3
import threading
from pathlib import Path
from threading import Thread

import discord
from discord.ext import commands
from flask import Flask


# =========================================================
# 1. سيرفر الويب لـ Render
# =========================================================

app = Flask("")


@app.route("/")
def home():
    return "LEON Bot is Online!"


@app.route("/api/healthz")
def healthz():
    return {
        "service": "LEON Bot",
        "status": "online",
    }


def run():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)


def keep_alive():
    thread = Thread(target=run, daemon=True)
    thread.start()


keep_alive()


# =========================================================
# 2. قواعد البيانات
# =========================================================

BASE_DIR = Path(__file__).resolve().parent

DATABASE_FILE = BASE_DIR / "leon_tickets.db"
ADMIN_DB_FILE = BASE_DIR / "admin_points.db"


# =========================================================
# 3. قاعدة بيانات الألعاب
# =========================================================

class GameDatabase:

    def __init__(self, path: Path):
        self.connection = sqlite3.connect(
            path,
            check_same_thread=False
        )
        self.connection.row_factory = sqlite3.Row
        self.lock = threading.RLock()

    def initialize(self):
        with self.lock:

            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS game_questions (
                    id INTEGER PRIMARY KEY,
                    kind TEXT NOT NULL,
                    question TEXT NOT NULL,
                    options TEXT NOT NULL,
                    correct_index INTEGER NOT NULL
                )
                """
            )

            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS game_scores (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    game_type TEXT NOT NULL,
                    score INTEGER NOT NULL,
                    total INTEGER NOT NULL,
                    played_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            self.connection.commit()


game_database = GameDatabase(DATABASE_FILE)
game_database.initialize()


# =========================================================
# 4. Discord Bot
# =========================================================

intents = discord.Intents.default()

intents.message_content = True
intents.members = True


bot = commands.Bot(
    command_prefix="!",
    intents=intents
)


# =========================================================
# 5. تحميل جميع أنظمة البوت
# =========================================================

@bot.setup_hook
async def setup_hook():

    print("========================================")
    print("🚀 LEON Bot setup started...")
    print("========================================")

    # -----------------------------------------------------
    # الألعاب
    # -----------------------------------------------------

    try:
        from games import register_game_commands

        register_game_commands(bot)

        print("✅ Games commands loaded.")

    except Exception as e:
        print(f"❌ Games loading error: {type(e).__name__}: {e}")


    # -----------------------------------------------------
    # نقاط الإدارة
    # -----------------------------------------------------

    try:
        import admin_points

        if hasattr(admin_points, "setup_admin_points"):

            await admin_points.setup_admin_points(bot)

            print("✅ Admin points loaded.")

        elif hasattr(admin_points, "setup"):

            result = admin_points.setup(bot)

            if result is not None:
                await result

            print("✅ Admin points setup loaded.")

        else:

            print(
                "⚠️ admin_points.py does not contain "
                "setup_admin_points() or setup()."
            )

    except Exception as e:

        print(
            f"❌ Admin points loading error: "
            f"{type(e).__name__}: {e}"
        )


    # -----------------------------------------------------
    # نظام التذاكر
    # -----------------------------------------------------
    #
    # البرنامج يحاول تحميل أكثر أسماء ملفات التذاكر
    # شيوعاً بدون ما يوقف البوت إذا الملف غير موجود.
    #

    ticket_loaded = False

    ticket_modules = [
        "tickets",
        "ticket",
        "ticket_system",
        "ticket_bot",
        "ticket_systems",
    ]

    for module_name in ticket_modules:

        if ticket_loaded:
            break

        try:

            module = __import__(module_name)

            # setup(bot)
            if hasattr(module, "setup"):

                result = module.setup(bot)

                if result is not None:
                    await result

                print(
                    f"✅ Ticket system loaded from "
                    f"{module_name}.py"
                )

                ticket_loaded = True
                break


            # setup_tickets(bot)
            if hasattr(module, "setup_tickets"):

                result = module.setup_tickets(bot)

                if result is not None:
                    await result

                print(
                    f"✅ Ticket system loaded from "
                    f"{module_name}.py"
                )

                ticket_loaded = True
                break


            # setup_ticket_system(bot)
            if hasattr(module, "setup_ticket_system"):

                result = module.setup_ticket_system(bot)

                if result is not None:
                    await result

                print(
                    f"✅ Ticket system loaded from "
                    f"{module_name}.py"
                )

                ticket_loaded = True
                break


            # register_commands(bot)
            if hasattr(module, "register_commands"):

                module.register_commands(bot)

                print(
                    f"✅ Ticket commands registered from "
                    f"{module_name}.py"
                )

                ticket_loaded = True
                break


        except ModuleNotFoundError:

            # الملف غير موجود، ننتقل للاسم التالي
            continue

        except Exception as e:

            print(
                f"❌ Ticket loading error in "
                f"{module_name}.py: "
                f"{type(e).__name__}: {e}"
            )

            break


    if not ticket_loaded:

        print(
            "⚠️ No ticket system module was found."
        )


    # -----------------------------------------------------
    # عرض جميع أوامر البوت قبل المزامنة
    # -----------------------------------------------------

    commands_list = bot.tree.get_commands()

    print(
        f"📋 Commands registered before sync: "
        f"{len(commands_list)}"
    )

    for command in commands_list:
        print(f"   └─ /{command.name}")


    # -----------------------------------------------------
    # مزامنة Slash Commands
    # -----------------------------------------------------

    try:

        synced = await bot.tree.sync()

        print(
            f"✅ Global sync completed: "
            f"{len(synced)} commands."
        )

    except Exception as e:

        print(
            f"❌ Global command sync failed: "
            f"{type(e).__name__}: {e}"
        )


    # -----------------------------------------------------
    # مزامنة الأوامر لكل سيرفر
    # -----------------------------------------------------

    for guild in bot.guilds:

        try:

            guild_synced = await bot.tree.sync(
                guild=guild
            )

            print(
                f"✅ Guild sync: "
                f"{guild.name} "
                f"({guild.id}) -> "
                f"{len(guild_synced)} commands"
            )

        except Exception as e:

            print(
                f"❌ Guild sync failed for "
                f"{guild.name}: "
                f"{type(e).__name__}: {e}"
            )


# =========================================================
# 6. on_ready
# =========================================================

@bot.event
async def on_ready():

    print("========================================")
    print(
        f"🤖 Logged in as: "
        f"{bot.user} ({bot.user.id})"
    )
    print(
        f"🌐 Connected to "
        f"{len(bot.guilds)} server(s)."
    )
    print("========================================")


# =========================================================
# 7. تشغيل البوت
# =========================================================

token = os.environ.get("DISCORD_TOKEN")


if not token:

    print(
        "❌ ERROR: DISCORD_TOKEN variable "
        "is missing from Environment Variables!"
    )

else:

    bot.run(token)
