"""
Live signal generation: reuses the exact validated modules from
~/earnings-bet-strategy (selection scoring, beat-streak logic, priority
ranking) applied to freshly-pulled current data instead of historical
backtest data. Finds tickers that (a) are in this quarter's top-150
selection and (b) report earnings tomorrow (or today, for AMC reports
that haven't dropped yet), with a qualifying beat streak.
"""
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

STRATEGY_REPO = Path.home() / "earnings-bet-strategy"
sys.path.insert(0, str(STRATEGY_REPO / "src"))
sys.path.insert(0, str(STRATEGY_REPO / "scripts"))

from earnings_bet_strategy.universe_selection import build_quarterly_scores, select_top_n  # noqa: E402
from earnings_bet_strategy.strategy import _beat_streaks  # noqa: E402
from priority_ranking import pre_earnings_analyst_score  # noqa: E402

from . import config, db, live_quote

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"


def _load_data():
    prices = pd.read_parquet(DATA_DIR / "live_prices.parquet")
    prices["datetime"] = pd.to_datetime(prices["datetime"])
    earnings = pd.read_parquet(DATA_DIR / "live_earnings.parquet")
    earnings["earnings_date"] = pd.to_datetime(earnings["earnings_date"]).dt.tz_localize(None)
    ratings = pd.read_parquet(DATA_DIR / "live_ratings.parquet")
    ratings["grade_date"] = pd.to_datetime(ratings["grade_date"])
    return prices, earnings, ratings


def find_todays_candidates(today=None):
    """
    Returns a list of dicts: ticker, entry_price (last close), recommended
    dollars, stop_price, path guess, next_earnings_date. One entry per
    ticker whose next report is imminent and qualifies.
    """
    today = today or date.today()
    prices, earnings, ratings = _load_data()
    tickers = sorted(set(prices["ticker"].unique()) & set(earnings["ticker"].unique()))

    this_quarter_start = pd.Timestamp(year=today.year, month=((today.month - 1) // 3) * 3 + 1, day=1)
    scores = build_quarterly_scores(tickers, earnings, prices, [this_quarter_start])
    selection = select_top_n(scores, config.TOP_N_SELECTION).get(this_quarter_start, set())

    candidates = []
    for t in tickers:
        if t not in selection:
            continue
        e = earnings[earnings["ticker"] == t].sort_values("earnings_date")
        upcoming = e[e["reported_eps"].isna() & e["earnings_date"].notna()]
        if upcoming.empty:
            continue
        next_report = upcoming.iloc[0]
        report_date = next_report["earnings_date"].date()
        if not (today <= report_date <= today + timedelta(days=2)):
            continue  # only surface signals for imminent reports

        prior_status = db.signal_status(t, report_date)
        if prior_status in ("entered", "skipped"):
            continue  # already acted on (or already expired/skipped) for this exact report -- never re-suggest it

        past = _beat_streaks(e[e["earnings_date"] < next_report["earnings_date"]])
        streak = 0
        for row in past.itertuples():
            if pd.notna(row.surprise_pct) and row.surprise_pct > 0:
                streak += 1
            else:
                streak = 0
        if streak < config.BEAT_STREAK_MIN:
            continue

        p = prices[prices["ticker"] == t].sort_values("datetime")
        live_price = live_quote.get_live_price(t)
        last_close = live_price if live_price is not None else float(p["close"].iloc[-1])
        analyst_score = pre_earnings_analyst_score(t, pd.Timestamp(today), ratings)
        q_score = scores[(scores["ticker"] == t) & (scores["quarter_start"] == this_quarter_start)]["score"]
        q_score = float(q_score.iloc[0]) if len(q_score) and pd.notna(q_score.iloc[0]) else 0.0

        candidates.append({
            "ticker": t, "report_date": report_date, "last_close": float(last_close),
            "priority": q_score + 5 * analyst_score, "beat_streak": streak,
        })

    candidates.sort(key=lambda c: c["priority"], reverse=True)
    _size_with_eviction(candidates, prices)

    for c in candidates:
        if db.signal_status(c["ticker"], c["report_date"]) is None:
            db.log_signal(c["ticker"], today, c["report_date"], c["beat_streak"], c["priority"], c["recommended_dollars"])

    return candidates


def _size_with_eviction(candidates, prices):
    """
    Matches the validated backtest's priority-ranked, eviction-aware
    capital allocation (not just naive FIFO/min(target,cash)): process
    candidates in priority order, and if cash is short, check whether the
    weakest currently-open position is enough weaker (by EVICT_MARGIN) to
    justify suggesting the user sell it early to fund this stronger signal.
    Mutates each candidate dict in place with recommended_dollars and,
    if applicable, an evict_suggestion.
    """
    equity = db.equity()
    cash = db.get_balance() or 0.0
    open_positions = {p["ticker"]: p for p in db.list_positions()}
    # open positions don't carry a stored priority in the DB (they were sized
    # at entry time) -- approximate with 0 so any real new signal with a
    # positive priority can out-rank an already-open position when cash is tight
    open_priorities = {t: 0.0 for t in open_positions}

    for c in candidates:
        target = equity / config.TARGET_SLOTS
        size = min(target, cash)
        if size < 1.0 and open_priorities:
            weakest_ticker = min(open_priorities, key=open_priorities.get)
            if c["priority"] >= open_priorities[weakest_ticker] + config.EVICT_MARGIN:
                pos = open_positions[weakest_ticker]
                p = prices[prices["ticker"] == weakest_ticker].sort_values("datetime")
                mark_price = float(p["close"].iloc[-1]) if len(p) else pos["entry_price"]
                cash += pos["shares"] * mark_price
                c["evict_suggestion"] = {"ticker": weakest_ticker, "at_price": round(mark_price, 2)}
                del open_priorities[weakest_ticker]
                size = min(target, cash)
        c["recommended_dollars"] = round(size, 2)
        cash -= size
