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

# strategy parameters. Entry/exit logic (BEAT_STREAK_MIN, ATR stop, MAX_HOLD_DAYS,
# etc.) matches data/trades_FINAL.parquet in earnings-bet-strategy --
# confirmed by regenerating all 4,389 trades from the current strategy.py
# code with these exact parameters and getting a 100.0% exact match on
# every trade's exit date. ATR_MULTIPLIER=2.5 really is part of this config
# -- it was set ad hoc in an interactive session and never saved to a
# script, which is why an earlier grep for it came up empty; the ATR-based
# stop (with an 8% fallback when ATR is NaN) is what actually produced the
# validated result, not the flat-8%-only version.
#
# TARGET_SLOTS=5 with the composite priority formula (see signal_engine.py)
# was chosen over the plain 10-slot/momentum+analyst version after a full
# concentration sweep + Monte Carlo validation: median bootstrap total
# +801% vs the 10-slot baseline's +566%, with an acceptable (not the
# lowest, but chosen deliberately) tail-risk profile. Note TARGET_SLOTS
# sizes each position at equity/N -- it does NOT cap how many positions
# can be open at once; a genuinely-hard-capped version was tested too but
# rejected for having ~3x the drawdown-tail risk for about the same
# median return.
TARGET_SLOTS = 5
BEAT_STREAK_MIN = 1
TRAILING_PEAK_DROP_PCT = 0.08  # ATR fallback only -- see exit_engine.py
ATR_WINDOW = 14
ATR_MULTIPLIER = 2.5
MAX_HOLD_DAYS = 40
EVICT_MARGIN = 2.0
TOP_N_SELECTION = 150
MIN_TRADE_DOLLARS = 1.0  # matches portfolio_sim_v2.py -- below this, the backtest
                         # skips the trade entirely rather than "funding" a nonsense
                         # fractional-cent position; the live bot must do the same
                         # instead of suggesting a real-money buy of a few cents
