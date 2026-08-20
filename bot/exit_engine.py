"""
Daily check on open positions: has the flat 8% trailing-peak stop been
hit (beat path), or has the position's next earnings report arrived
(held path, exit the day before it drops)?

The beat path uses a flat trailing-peak percentage stop, not ATR --
confirmed by checking every sweep/validation script in the
earnings-bet-strategy repo (none of them ever set an ATR multiplier)
and docs/STRATEGY.md, which documents the exit as a flat 8% stop.
An ATR-based version of this file existed briefly but had no basis in
any actual backtest run and was removed.
"""
from datetime import date
from pathlib import Path

import pandas as pd

from . import config, db, live_quote

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"


def classify_pending_positions(today=None):
    """
    A freshly-/entered position has no path yet (we don't know beat vs.
    held until the report actually drops and the reaction is visible).
    Once there's at least one close after entry, classify it: price up
    since entry -> 'beat' path (trailing-peak stop); price down -> 'held'
    path (look up the real next earnings date, hold until then).
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
    earnings = None  # loaded lazily -- only needed for held positions missing a next_earnings_date

    alerts = []
    for pos in db.list_positions():
        t = pos["ticker"]
        p = prices[prices["ticker"] == t].sort_values("datetime").reset_index(drop=True)
        if p.empty:
            continue
        # use a live intraday quote if we can get one (bot runs 30 min before close,
        # so the daily parquet's last row is still yesterday's) -- falls back to the
        # daily close if the live fetch fails for any reason
        live_price = live_quote.get_live_price(t)
        last_close = live_price if live_price is not None else float(p["close"].iloc[-1])

        if pos["path"] == "beat":
            new_peak = max(pos["peak_price"], last_close)
            if new_peak != pos["peak_price"]:
                db.update_peak_price(t, new_peak)
            stop_level = new_peak * (1 - config.TRAILING_PEAK_DROP_PCT)
            if last_close <= stop_level:
                alerts.append({"ticker": t, "reason": f"trailing-peak stop hit ({config.TRAILING_PEAK_DROP_PCT:.0%} off peak)",
                                "current_price": last_close, "stop_level": round(stop_level, 2)})
                continue
            # backtest force-exits the beat/trailing-stop path at MAX_HOLD_DAYS trading days
            # past the reaction if the stop is never hit -- without this a position that
            # just drifts sideways above its stop would ride forever, live-only behavior
            # the backtest never actually produced
            days_since_entry = len(p[p["datetime"] > pd.Timestamp(pos["entry_date"]).tz_localize(None)])
            if days_since_entry >= config.MAX_HOLD_DAYS:
                alerts.append({"ticker": t, "reason": f"max hold ({config.MAX_HOLD_DAYS} trading days) reached",
                                "current_price": last_close})
        elif pos["path"] == "held":
            next_date_str = pos["next_earnings_date"]
            if not next_date_str:
                # the next report wasn't known yet when this position was classified
                # (e.g. it wasn't in the earnings horizon pulled that day) -- retry from
                # today's fresh earnings data instead of leaving this position stuck with
                # no exit condition for the rest of its life
                if earnings is None:
                    earnings = pd.read_parquet(DATA_DIR / "live_earnings.parquet")
                    earnings["earnings_date"] = pd.to_datetime(earnings["earnings_date"]).dt.tz_localize(None)
                entry_dt = pd.Timestamp(pos["entry_date"]).tz_localize(None)
                e = earnings[earnings["ticker"] == t].sort_values("earnings_date")
                upcoming = e[e["earnings_date"] > entry_dt]
                if len(upcoming):
                    next_date_str = str(upcoming.iloc[0]["earnings_date"].date())
                    db_update_path_held(t, next_date_str)
                else:
                    continue  # still unknown, try again next run
            next_date = pd.Timestamp(next_date_str).date()
            if (next_date - today).days <= 1:
                alerts.append({"ticker": t, "reason": "next earnings report imminent -- exit before it drops",
                                "current_price": last_close, "next_earnings_date": str(next_date)})
    return alerts
