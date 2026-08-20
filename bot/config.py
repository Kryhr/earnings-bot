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

# strategy parameters -- matches data/trades_FINAL.parquet in
# earnings-bet-strategy (+566.4% total 2018-2026, one negative year: 2022
# at -9.6%). Confirmed by regenerating all 4,389 trades from the current
# strategy.py code with these exact parameters and getting a 100.0% exact
# match on every trade's exit date, then re-running the full portfolio
# simulation (simulate_with_eviction, evict_margin=2.0, TARGET_SLOTS=10)
# and getting the identical +566.4% total and identical year-by-year
# returns. ATR_MULTIPLIER=2.5 really is part of this config -- an earlier
# pass this session removed it after finding no *script* set it, but it
# was set ad hoc in an earlier interactive session and never saved to a
# script; the ATR-based stop (with an 8% fallback when ATR is NaN) is
# what actually produced this result, not the flat-8%-only version.
TARGET_SLOTS = 10
BEAT_STREAK_MIN = 1
TRAILING_PEAK_DROP_PCT = 0.08  # ATR fallback only -- see exit_engine.py
ATR_WINDOW = 14
ATR_MULTIPLIER = 2.5
MAX_HOLD_DAYS = 40
EVICT_MARGIN = 2.0
TOP_N_SELECTION = 150
