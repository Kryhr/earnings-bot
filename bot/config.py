"""
Loads secrets from a local .env file (never committed, never pasted into
chat). Copy .env.example to .env and fill in the real values.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
DISCORD_CHANNEL_ID = os.environ.get("DISCORD_CHANNEL_ID")  # where signal/exit alerts get posted

DB_PATH = ROOT / "data" / "bot_state.db"

# strategy parameters, must match the validated final backtest config
TARGET_SLOTS = 10
BEAT_STREAK_MIN = 1
ATR_MULTIPLIER = 2.5
ATR_WINDOW = 14
MAX_HOLD_DAYS = 40
EVICT_MARGIN = 2.0
TOP_N_SELECTION = 150
