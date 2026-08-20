"""
Loads secrets from a local .env file (never committed, never pasted into
chat). Copy .env.example to .env and fill in the real values.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
# override=True: without this, an already-set (e.g. stale/empty) OS
# environment variable silently wins over .env, which is a common and
# very confusing cause of "improper token" errors -- the .env value never
# even gets used, with no error until discord.py rejects the wrong value.
loaded = load_dotenv(ENV_PATH, override=True)

DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
DISCORD_CHANNEL_ID = os.environ.get("DISCORD_CHANNEL_ID")  # where signal/exit alerts get posted

if __name__ == "__main__":
    print(f"Looking for .env at: {ENV_PATH}")
    print(f".env file found and loaded: {loaded}")
    print(f"DISCORD_BOT_TOKEN set: {bool(DISCORD_BOT_TOKEN)}  (length: {len(DISCORD_BOT_TOKEN) if DISCORD_BOT_TOKEN else 0})")
    print(f"DISCORD_CHANNEL_ID set: {bool(DISCORD_CHANNEL_ID)}  (value: {DISCORD_CHANNEL_ID!r})")
    if DISCORD_BOT_TOKEN:
        print(f"Token repr (checking for hidden whitespace/quotes): {DISCORD_BOT_TOKEN!r}")

DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "bot_state.db"

# strategy parameters, must match the validated final backtest config
TARGET_SLOTS = 10
BEAT_STREAK_MIN = 1
ATR_MULTIPLIER = 2.5
ATR_WINDOW = 14
MAX_HOLD_DAYS = 40
EVICT_MARGIN = 2.0
TOP_N_SELECTION = 150
