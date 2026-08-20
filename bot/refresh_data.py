"""
Pulls fresh daily price bars, earnings history, and analyst ratings for the
same 287-ticker universe validated in the backtest. Run this once daily
(the scheduler in bot.py triggers it) so signal/exit checks work off
current data. Reuses the same candidate list as ~/earnings-bet-strategy so
the live universe matches what was backtested.
"""
import sys
from pathlib import Path

import pandas as pd
import yfinance as yf

STRATEGY_REPO = Path.home() / "earnings-bet-strategy"
sys.path.insert(0, str(STRATEGY_REPO / "data"))
from candidate_universe import CANDIDATE_POOL  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"


def refresh_prices():
    data = yf.download(CANDIDATE_POOL, period="2y", interval="1d", group_by="ticker",
                        auto_adjust=True, progress=False, threads=True)
    frames = []
    for t in CANDIDATE_POOL:
        try:
            df = data[t].dropna(how="all")
        except KeyError:
            continue
        if df.empty:
            continue
        df = df.reset_index().rename(columns={
            "Date": "datetime", "Open": "open", "High": "high",
            "Low": "low", "Close": "close", "Volume": "volume",
        })
        df["ticker"] = t
        frames.append(df[["ticker", "datetime", "open", "high", "low", "close", "volume"]])
    out = pd.concat(frames, ignore_index=True)
    out.to_parquet(DATA_DIR / "live_prices.parquet", index=False)
    return set(out["ticker"].unique())


def refresh_earnings_and_ratings(tickers):
    e_frames, ud_frames = [], []
    for t in tickers:
        try:
            df = yf.Ticker(t).get_earnings_dates(limit=28)
        except Exception:
            df = None
        if df is not None and not df.empty:
            df = df.reset_index().rename(columns={
                "Earnings Date": "earnings_date", "EPS Estimate": "eps_estimate",
                "Reported EPS": "reported_eps", "Surprise(%)": "surprise_pct",
            })
            df["ticker"] = t
            e_frames.append(df[["ticker", "earnings_date", "eps_estimate", "reported_eps", "surprise_pct"]])
        try:
            ud = yf.Ticker(t).upgrades_downgrades
        except Exception:
            ud = None
        if ud is not None and not ud.empty:
            ud = ud.reset_index().rename(columns={"GradeDate": "grade_date"})
            ud["ticker"] = t
            ud_frames.append(ud)
    pd.concat(e_frames, ignore_index=True).to_parquet(DATA_DIR / "live_earnings.parquet", index=False)
    pd.concat(ud_frames, ignore_index=True).to_parquet(DATA_DIR / "live_ratings.parquet", index=False)


def main():
    print("Refreshing prices...")
    tickers = refresh_prices()
    print(f"  {len(tickers)} tickers")
    print("Refreshing earnings/ratings...")
    refresh_earnings_and_ratings(sorted(tickers))
    print("Done.")


if __name__ == "__main__":
    main()
