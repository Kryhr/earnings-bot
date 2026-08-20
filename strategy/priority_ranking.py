"""
Priority score for allocating scarce capital when multiple entry signals
compete on the same day: combines the quarterly stock-selection quality
score (momentum + crash-risk, already computed) with a pre-earnings
analyst-sentiment trend (net rating/price-target direction in the 45 days
BEFORE entry -- real historical dates, causal, unlike the dead-end
estimate-revision data).
"""
import pandas as pd


def pre_earnings_analyst_score(ticker, entry_date, ud_df, window_days=45):
    ud = ud_df[ud_df["ticker"] == ticker]
    window_start = entry_date - pd.Timedelta(days=window_days)
    window = ud[(ud["grade_date"] >= window_start) & (ud["grade_date"] < entry_date)]
    window = window[window["priorPriceTarget"] > 0]
    if window.empty:
        return 0.0  # no data -- neutral, not a penalty
    return (window["currentPriceTarget"] - window["priorPriceTarget"]).mean() / window["priorPriceTarget"].mean()


def add_priority_scores(trades_df, ud_df, quarterly_scores_df):
    """
    trades_df needs: ticker, entry_date, quarter_start.
    Returns trades_df with an added 'priority' column (higher = fund first).
    """
    ud = ud_df.copy()
    ud["grade_date"] = pd.to_datetime(ud["grade_date"])

    qscore_lookup = quarterly_scores_df.set_index(["ticker", "quarter_start"])["score"].to_dict()

    priorities = []
    for row in trades_df.itertuples():
        analyst = pre_earnings_analyst_score(row.ticker, row.entry_date, ud)
        qscore = qscore_lookup.get((row.ticker, row.quarter_start))
        qscore = qscore if qscore == qscore else 0.0  # NaN guard
        priorities.append(float(qscore) + 5 * analyst)  # analyst pct-change scaled up to matter
    trades_df = trades_df.copy()
    trades_df["priority"] = priorities
    return trades_df
