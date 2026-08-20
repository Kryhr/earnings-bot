"""
Live signal generation: reuses the exact validated strategy modules,
vendored into strategy/ (selection scoring, beat-streak logic, priority
ranking) applied to freshly-pulled current data instead of historical
backtest data. Finds tickers that (a) are in this quarter's top-150
selection and (b) report earnings tomorrow (or today, for AMC reports
that haven't dropped yet), with a qualifying beat streak.
"""
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from strategy.universe_selection import build_quarterly_scores, select_top_n
from strategy.strategy import _beat_streaks
from strategy.priority_ranking import pre_earnings_analyst_score

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


def _load_revenue():
    """
    Optional -- build_quarterly_scores() falls back to a momentum+crash-risk
    -only score if this file doesn't exist yet (e.g. before the first EDGAR
    pull finishes), but every day after that it should be present.
    """
    path = DATA_DIR / "edgar_revenue.parquet"
    if not path.exists():
        return None
    revenue = pd.read_parquet(path)
    revenue["period_end"] = pd.to_datetime(revenue["period_end"])
    return revenue


def _entry_date_for(earnings_ts, ticker_prices):
    """
    Mirrors strategy.py's _entry_and_reaction_idx exactly: AMC reports
    (hour >= 12) are entered at that same day's close, before the print
    drops that evening. BMO reports are entered the trading day strictly
    before, since by the report date itself the number is already out.
    Returns (entry_date, is_amc), or (None, is_amc) if there's no prior
    trading day on record yet to anchor a BMO entry to.
    """
    report_date = earnings_ts.date()
    is_amc = earnings_ts.hour >= 12
    if is_amc:
        return report_date, is_amc
    trading_days = sorted(set(ticker_prices["datetime"].dt.date))
    prior = [d for d in trading_days if d < report_date]
    if not prior:
        return None, is_amc
    return prior[-1], is_amc


def find_todays_candidates(today=None):
    """
    Returns a list of dicts: ticker, entry_price (last close), recommended
    dollars, stop_price, path guess, next_earnings_date. One entry per
    ticker whose next report is imminent and qualifies.
    """
    today = today or date.today()
    prices, earnings, ratings = _load_data()
    revenue = _load_revenue()
    tickers = sorted(set(prices["ticker"].unique()) & set(earnings["ticker"].unique()))

    this_quarter_start = pd.Timestamp(year=today.year, month=((today.month - 1) // 3) * 3 + 1, day=1)
    scores = build_quarterly_scores(tickers, earnings, prices, [this_quarter_start], revenue_df=revenue)
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
        if not (today <= report_date <= today + timedelta(days=4)):
            # _entry_date_for's "last trading day before report_date" only means
            # anything for a report that's actually near -- for anything further
            # out, the "last trading day we have prices for" is just today, which
            # would wrongly look like a same-day BMO entry for a report months
            # away. Bound to a near-term window before computing the exact day.
            continue
        entry_date, is_amc = _entry_date_for(next_report["earnings_date"], prices[prices["ticker"] == t])
        if entry_date is None or entry_date != today:
            # not just "close to" the report -- must be exactly the day the backtest
            # would have entered on (today for AMC, the day before for BMO). A BMO
            # report whose report_date is today already happened this morning --
            # suggesting a buy "today" for it would be entering after the fact.
            continue

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
            "momentum_score": q_score, "analyst_score": analyst_score,
        })

    candidates.sort(key=lambda c: c["priority"], reverse=True)
    for i, c in enumerate(candidates, start=1):
        c["rank"] = i
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

    Cash isn't deducted in the DB until /entered is actually called, so a
    signal the user hasn't confirmed yet still shows as spendable balance.
    Without reserving it here, a second signal firing before the first is
    confirmed would get sized against the same dollars -- silently telling
    the user to buy two things with money that only covers one. Pending
    signals for tickers already in this candidate batch don't get double-
    reserved (they're the same money being re-quoted, not new competition).
    """
    equity = db.equity()
    cash = db.get_balance() or 0.0
    candidate_tickers = {c["ticker"] for c in candidates}
    reserved = sum(
        s["recommended_dollars"] for s in db.pending_signals()
        if s["ticker"] not in candidate_tickers
    )
    cash = max(0.0, cash - reserved)
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
