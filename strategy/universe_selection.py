"""
Quarterly stock-selection layer: ranks the candidate pool at each quarter
boundary using only data available before that boundary (causal), and
picks the top N for that quarter's earnings-bet trades.

Score = momentum z-score + crash-risk z-score + revenue-growth z-score:
  - momentum: trailing 126-trading-day price return as of the quarter start
  - crash risk: worst single-day earnings-reaction return over the
    trailing 8 reports before the quarter start (a real, ticker-specific
    tail-risk signal -- this is exactly the kind of thing that would have
    flagged ZTS's ~-18% overnight gap before it happened, not just after)
  - revenue growth: most recent YoY quarterly revenue growth (real SEC
    EDGAR actuals, not estimates) as of the quarter start -- "how is the
    actual business doing," independent of stock price action
"""
import pandas as pd


def _revenue_growth(ticker, revenue_df):
    """
    List of (available_date, yoy_growth) for this ticker, using real EDGAR
    actuals. Uses the filing date, not the fiscal period_end, as the
    "available as of" date -- a quarter's real revenue isn't publicly known
    until the 10-Q/10-K is actually filed (~30-45 days after period end),
    so using period_end directly would be a lookahead leak.
    """
    r = revenue_df[revenue_df["ticker"] == ticker].sort_values("period_end").reset_index(drop=True)
    if len(r) < 5:
        return []
    growths = []
    for i in range(4, len(r)):
        gap_days = (r["period_end"].iloc[i] - r["period_end"].iloc[i - 4]).days
        if 300 <= gap_days <= 430 and r["revenue"].iloc[i - 4] > 0:
            growth = r["revenue"].iloc[i] / r["revenue"].iloc[i - 4] - 1
            available_date = r["filed"].iloc[i] if pd.notna(r["filed"].iloc[i]) else r["period_end"].iloc[i] + pd.Timedelta(days=45)
            growths.append((pd.Timestamp(available_date), growth))
    return growths


def _earnings_day_moves(ticker, earnings_df, price_df):
    """List of (earnings_date, day1_return) for this ticker's past reports."""
    e = earnings_df[earnings_df["ticker"] == ticker].sort_values("earnings_date")
    p = price_df[price_df["ticker"] == ticker].sort_values("datetime").reset_index(drop=True)
    if p.empty:
        return []
    p["date"] = p["datetime"].dt.date
    date_to_idx = {d: i for i, d in enumerate(p["date"])}
    dates_sorted = p["date"].tolist()

    moves = []
    for row in e.itertuples():
        if pd.isna(row.earnings_date) or pd.isna(row.surprise_pct):
            continue
        report_date = row.earnings_date.date()
        is_amc = row.earnings_date.hour >= 12
        if is_amc:
            entry_idx = date_to_idx.get(report_date)
            reaction_idx = entry_idx + 1 if entry_idx is not None else None
        else:
            later = [d for d in dates_sorted if d >= report_date]
            reaction_idx = date_to_idx[later[0]] if later else None
            entry_idx = reaction_idx - 1 if reaction_idx is not None else None
        if entry_idx is None or reaction_idx is None or reaction_idx >= len(p) or entry_idx < 0:
            continue
        entry_close = p["close"].iloc[entry_idx]
        reaction_close = p["close"].iloc[reaction_idx]
        moves.append((row.earnings_date, reaction_close / entry_close - 1))
    return moves


def build_quarterly_scores(tickers, earnings_df, price_df, quarter_starts, trailing_reports=8, revenue_df=None):
    """
    Returns DataFrame: ticker, quarter_start, momentum, worst_move,
    revenue_growth (only populated if revenue_df given), score.
    score is None where insufficient data -- excluded from ranking that
    quarter, not assumed good or bad. Revenue growth is optional so this
    stays backward compatible with callers that don't have EDGAR data.
    """
    price_by_ticker = {t: price_df[price_df["ticker"] == t].sort_values("datetime").reset_index(drop=True) for t in tickers}
    moves_by_ticker = {t: _earnings_day_moves(t, earnings_df, price_df) for t in tickers}
    rev_by_ticker = {t: _revenue_growth(t, revenue_df) for t in tickers} if revenue_df is not None else {}

    rows = []
    for qs in quarter_starts:
        for t in tickers:
            p = price_by_ticker[t]
            prior = p[p["datetime"] < qs]
            momentum = None
            if len(prior) >= 126:
                momentum = prior["close"].iloc[-1] / prior["close"].iloc[-126] - 1

            past_moves = [m for d, m in moves_by_ticker[t] if d < qs]
            worst_move = None
            if len(past_moves) >= 3:  # need a minimal sample to trust it
                worst_move = min(past_moves[-trailing_reports:])

            revenue_growth = None
            if revenue_df is not None:
                past_growths = [g for d, g in rev_by_ticker.get(t, []) if d < qs]
                if past_growths:
                    revenue_growth = past_growths[-1]  # most recent available growth figure

            rows.append({"ticker": t, "quarter_start": qs, "momentum": momentum,
                         "worst_move": worst_move, "revenue_growth": revenue_growth})

    df = pd.DataFrame(rows)
    df["score"] = None
    use_revenue = revenue_df is not None
    required_cols = ["momentum", "worst_move"] + (["revenue_growth"] if use_revenue else [])
    for qs, g in df.groupby("quarter_start"):
        valid = g.dropna(subset=required_cols)
        if len(valid) < 10:
            continue
        mom_z = (valid["momentum"] - valid["momentum"].mean()) / valid["momentum"].std()
        risk_z = (valid["worst_move"] - valid["worst_move"].mean()) / valid["worst_move"].std()  # higher (less negative) worst_move = safer
        score = mom_z + risk_z
        if use_revenue:
            rev_z = (valid["revenue_growth"] - valid["revenue_growth"].mean()) / valid["revenue_growth"].std()
            score = score + rev_z
        df.loc[valid.index, "score"] = score
    return df


def select_top_n(scores_df, top_n):
    """quarter_start -> set of selected tickers for that quarter."""
    selection = {}
    for qs, g in scores_df.groupby("quarter_start"):
        ranked = g.dropna(subset=["score"]).sort_values("score", ascending=False)
        selection[qs] = set(ranked["ticker"].head(top_n))
    return selection
