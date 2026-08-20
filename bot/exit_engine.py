"""
Daily check on open positions: has the ATR trailing stop been hit (beat
path), or has the position's next earnings report arrived (held path,
exit the day before it drops)?
"""
import sys
from datetime import date
from pathlib import Path

import pandas as pd

STRATEGY_REPO = Path.home() / "earnings-bet-strategy"
sys.path.insert(0, str(STRATEGY_REPO / "src"))

from . import config, db

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"


def _atr(price_df, window=config.ATR_WINDOW):
    prev_close = price_df["close"].shift(1)
    tr = pd.concat([
        price_df["high"] - price_df["low"],
        (price_df["high"] - prev_close).abs(),
        (price_df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(window).mean()


def classify_pending_positions(today=None):
    """
    A freshly-/entered position has no path yet (we don't know beat vs.
    held until the report actually drops and the reaction is visible).
    Once there's at least one close after entry, classify it: price up
    since entry -> 'beat' path (start an ATR trailing stop); price down
    -> 'held' path (look up the real next earnings date, hold until then).
    Returns a list of dicts describing what was classified, for alerting.
    """
    today = today or date.today()
    prices = pd.read_parquet(DATA_DIR / "live_prices.parquet")
    prices["datetime"] = pd.to_datetime(prices["datetime"])
    earnings = pd.read_parquet(DATA_DIR / "live_earnings.parquet")
    earnings["earnings_date"] = pd.to_datetime(earnings["earnings_date"]).dt.tz_localize(None)

    results = []
    for pos in db.list_positions():
        if pos["path"] is not None:
            continue
        t = pos["ticker"]
        p = prices[prices["ticker"] == t].sort_values("datetime").reset_index(drop=True)
        entry_dt = pd.Timestamp(pos["entry_date"]).tz_localize(None)
        after_entry = p[p["datetime"] > entry_dt]
        if after_entry.empty:
            continue  # report hasn't reacted yet, check again tomorrow

        reaction_close = float(after_entry["close"].iloc[0])
        went_up = reaction_close >= pos["entry_price"]
        if went_up:
            db_update_path_beat(t, reaction_close)
            results.append({"ticker": t, "path": "beat", "reaction_price": reaction_close})
        else:
            e = earnings[earnings["ticker"] == t].sort_values("earnings_date")
            upcoming = e[e["earnings_date"] > entry_dt]
            next_date = str(upcoming.iloc[0]["earnings_date"].date()) if len(upcoming) else None
            db_update_path_held(t, next_date)
            results.append({"ticker": t, "path": "held", "reaction_price": reaction_close, "next_earnings_date": next_date})
    return results


def db_update_path_beat(ticker, peak_price):
    import sqlite3
    conn = sqlite3.connect(config.DB_PATH)
    conn.execute("UPDATE positions SET path='beat', peak_price=? WHERE ticker=?", (peak_price, ticker))
    conn.commit()
    conn.close()


def db_update_path_held(ticker, next_earnings_date):
    import sqlite3
    conn = sqlite3.connect(config.DB_PATH)
    conn.execute("UPDATE positions SET path='held', next_earnings_date=? WHERE ticker=?", (next_earnings_date, ticker))
    conn.commit()
    conn.close()


def check_exits(today=None):
    """Returns a list of dicts: ticker, reason, current_price, suggested action."""
    today = today or date.today()
    prices = pd.read_parquet(DATA_DIR / "live_prices.parquet")
    prices["datetime"] = pd.to_datetime(prices["datetime"])

    alerts = []
    for pos in db.list_positions():
        t = pos["ticker"]
        p = prices[prices["ticker"] == t].sort_values("datetime").reset_index(drop=True)
        if p.empty:
            continue
        last_close = float(p["close"].iloc[-1])

        if pos["path"] == "beat":
            new_peak = max(pos["peak_price"], last_close)
            if new_peak != pos["peak_price"]:
                db.update_peak_price(t, new_peak)
            atr = _atr(p).iloc[-1]
            if pd.notna(atr):
                stop_level = new_peak - config.ATR_MULTIPLIER * atr
                if last_close <= stop_level:
                    alerts.append({"ticker": t, "reason": "ATR trailing stop hit",
                                    "current_price": last_close, "stop_level": round(stop_level, 2)})
        elif pos["path"] == "held" and pos["next_earnings_date"]:
            next_date = pd.Timestamp(pos["next_earnings_date"]).date()
            if (next_date - today).days <= 1:
                alerts.append({"ticker": t, "reason": "next earnings report imminent -- exit before it drops",
                                "current_price": last_close, "next_earnings_date": str(next_date)})
    return alerts
