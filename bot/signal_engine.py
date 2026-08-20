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


def _ticker_factors(ticker, earnings, prices, today):
    """
    Causal trailing stats for the composite priority formula, using only
    reports strictly before `today` (no lookahead):
      - trail_mean_surprise / trail_std_surprise: mean/std of past EPS
        surprise % -- rewards consistent beaters, not just frequent ones
      - trail_reaction_mag: mean price-reaction return over past BEATS only
        -- "does this stock actually pop when it beats," which plain
        beat-streak never captures
    Reaction return uses the same entry/reaction relationship as the
    backtest: reaction_date is always the next trading day after
    entry_date (true whether the report was BMO or AMC), so it can reuse
    _entry_date_for directly instead of re-deriving is_amc logic.
    Returns (mean_surprise, std_surprise, reaction_mag), any of which may
    be None if there isn't enough history yet.
    """
    e = earnings[(earnings["ticker"] == ticker) & (earnings["earnings_date"] < pd.Timestamp(today))
                 & earnings["surprise_pct"].notna()].sort_values("earnings_date")
    if e.empty:
        return None, None, None
    p = prices[prices["ticker"] == ticker].sort_values("datetime").reset_index(drop=True)
    if p.empty:
        return None, None, None
    date_to_idx = {d: i for i, d in enumerate(p["datetime"].dt.date)}

    reaction_rets = []
    for row in e.itertuples():
        entry_date, _ = _entry_date_for(row.earnings_date, p)
        if entry_date is None or entry_date not in date_to_idx:
            continue
        entry_idx = date_to_idx[entry_date]
        if entry_idx + 1 >= len(p):
            continue
        entry_px = p["close"].iloc[entry_idx]
        reaction_px = p["close"].iloc[entry_idx + 1]
        reaction_rets.append((row.surprise_pct, reaction_px / entry_px - 1))

    mean_surprise = float(e["surprise_pct"].mean())
    std_surprise = float(e["surprise_pct"].std()) if len(e) >= 2 else None
    beat_reactions = [r for s, r in reaction_rets if s > 0]
    reaction_mag = float(sum(beat_reactions) / len(beat_reactions)) if beat_reactions else None
    return mean_surprise, std_surprise, reaction_mag


def _zscore(s):
    std = s.std()
    if not std or pd.isna(std):
        return pd.Series(0.0, index=s.index)
    return (s - s.mean()) / std


def find_todays_candidates(today=None):
    """
    Returns a list of dicts: ticker, entry_price (last close), recommended
    dollars, stop_price, path guess, next_earnings_date. One entry per
    ticker whose next report is imminent and qualifies.
    """
    today = today or date.today()
    prices, earnings, ratings = _load_data()
    tickers = sorted(set(prices["ticker"].unique()) & set(earnings["ticker"].unique()))

    # deliberately no revenue_df here -- confirmed by regenerating the actual
    # validated +566.4% trade set that momentum+crash-risk-only selection is
    # what produced it. Revenue growth was explored (edgar_revenue.parquet
    # exists) but never made it into the locked formula; docs/STRATEGY.md
    # itself says the revenue-growth factor was still "pending re-test."
    this_quarter_start = pd.Timestamp(year=today.year, month=((today.month - 1) // 3) * 3 + 1, day=1)
    scores = build_quarterly_scores(tickers, earnings, prices, [this_quarter_start])
    selection = select_top_n(scores, config.TOP_N_SELECTION).get(this_quarter_start, set())

    # the backtest models exactly one open trade per ticker per earnings cycle --
    # never a second position stacked on top of one already held. Without this,
    # a ticker sitting in the 'held' path (waiting out a miss until its next
    # report) could get suggested again as a brand-new buy for that same
    # upcoming report, effectively doubling exposure the backtest never modeled.
    held_tickers = {p["ticker"] for p in db.list_positions()}

    candidates = []
    for t in tickers:
        if t not in selection or t in held_tickers:
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

        candidates.append({
            "ticker": t, "report_date": report_date, "last_close": float(last_close),
            "beat_streak": streak,
        })

    # Composite priority (the "new" formula, chosen over plain momentum+analyst
    # after Monte Carlo validation showed it more robust): z(trailing mean
    # surprise) + z(-trailing surprise std, reward consistency) +
    # z(historical reaction magnitude on past beats) + z(quarterly
    # momentum/crash-risk score) + 5x analyst sentiment. Z-scored
    # cross-sectionally against the FULL top-150 selection pool for this
    # quarter (matching the backtest's per-quarter z-scoring population),
    # not just against today's tiny handful of actual candidates -- with
    # only 1-2 names reporting on a given day, a same-day-only z-score
    # would be statistically meaningless (std often 0).
    if candidates:
        pool_rows = []
        for t in selection:
            mean_s, std_s, react_mag = _ticker_factors(t, earnings, prices, today)
            q_score = scores[(scores["ticker"] == t) & (scores["quarter_start"] == this_quarter_start)]["score"]
            q_score = float(q_score.iloc[0]) if len(q_score) and pd.notna(q_score.iloc[0]) else 0.0
            analyst_score = pre_earnings_analyst_score(t, pd.Timestamp(today), ratings)
            pool_rows.append({"ticker": t, "mean_surprise": mean_s, "std_surprise": std_s,
                               "reaction_mag": react_mag, "qscore": q_score, "analyst_score": analyst_score})
        pool = pd.DataFrame(pool_rows)
        pool["mean_surprise"] = pool["mean_surprise"].fillna(pool["mean_surprise"].median())
        pool["std_surprise"] = pool["std_surprise"].fillna(pool["std_surprise"].median())
        pool["reaction_mag"] = pool["reaction_mag"].fillna(pool["reaction_mag"].median())
        pool["z_surprise"] = _zscore(pool["mean_surprise"])
        pool["z_consistency"] = _zscore(-pool["std_surprise"])
        pool["z_reaction_mag"] = _zscore(pool["reaction_mag"])
        pool["z_qscore"] = _zscore(pool["qscore"])
        pool["priority"] = (pool["z_surprise"] + pool["z_consistency"] + pool["z_reaction_mag"]
                             + pool["z_qscore"] + 5 * pool["analyst_score"])
        pool_indexed = pool.set_index("ticker")
        for c in candidates:
            row = pool_indexed.loc[c["ticker"]]
            c["priority"] = float(row["priority"])
            c["momentum_score"] = float(row["qscore"])
            c["analyst_score"] = float(row["analyst_score"])
            c["avg_surprise_pct"] = float(row["mean_surprise"])
            c["reaction_magnitude_pct"] = float(row["reaction_mag"])

    candidates.sort(key=lambda c: c["priority"], reverse=True)
    for i, c in enumerate(candidates, start=1):
        c["rank"] = i
    _size_with_eviction(candidates, prices)

    # a signal that ends up sized below MIN_TRADE_DOLLARS (cash too tight, and
    # not a strong enough case to evict anything) isn't a real actionable
    # trade -- the backtest itself would have just skipped it (portfolio_sim_v2's
    # own MIN_TRADE_DOLLARS check), not "funded" a few cents. Never suggest a
    # buy the user can't sensibly execute, and don't log it as a pending
    # signal either -- there's nothing to reserve cash for or later mark entered.
    for c in candidates:
        if c["recommended_dollars"] < config.MIN_TRADE_DOLLARS:
            c["insufficient_cash"] = True
        elif db.signal_status(c["ticker"], c["report_date"]) is None:
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
    # each open position's priority is the real score it was funded at
    # (recorded via /entered), not a placeholder -- a genuinely strong
    # holding should never look artificially weak in an eviction comparison
    open_priorities = {t: p["priority"] for t, p in open_positions.items()}

    for c in candidates:
        target = equity / config.TARGET_SLOTS
        size = min(target, cash)
        if size < config.MIN_TRADE_DOLLARS and open_priorities:
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
