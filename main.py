import os
from threading import Thread
import discord
from discord.ext import commands
from flask import Flask

# --- 1. سيرفر الويب لتجاوز خمول Render ---
app = Flask("")


@app.route("/")
def home():
    return "Bot is alive!"


def run():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)


def keep_alive():
    t = Thread(target=run)
    t.start()


keep_alive()

# --- 2. كود البوت الخاص بك ---
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")


bot.run(os.environ.get("DISCORD_TOKEN"))
