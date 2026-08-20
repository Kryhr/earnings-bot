"""
Pulls fresh daily price bars, earnings history, and analyst ratings for the
same 287-ticker universe validated in the backtest. Run this once daily
(the scheduler in bot.py triggers it) so signal/exit checks work off
current data. Uses the vendored candidate list in strategy/ so this repo
is fully self-contained -- no dependency on the backtest repo existing on
whatever machine the bot runs on.
"""
from pathlib import Path

import pandas as pd
import yfinance as yf

from strategy.candidate_universe import CANDIDATE_POOL

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
    total = len(tickers)
    failures = []
    for i, t in enumerate(tickers):
        if i % 20 == 0:
            print(f"  ...{i}/{total}", flush=True)
        # each ticker is fully isolated -- a weird/unexpected data shape from
        # one ticker (missing columns, malformed structure, anything) must
        # never take down the whole run. Previously only the fetch call
        # itself was guarded; the processing right after (rename/column
        # selection) wasn't, so an odd response could crash the entire
        # script most of the way through a 10-20 minute run.
        try:
            df = yf.Ticker(t).get_earnings_dates(limit=28)
            if df is not None and not df.empty:
                df = df.reset_index().rename(columns={
                    "Earnings Date": "earnings_date", "EPS Estimate": "eps_estimate",
                    "Reported EPS": "reported_eps", "Surprise(%)": "surprise_pct",
                })
                df["ticker"] = t
                needed = ["ticker", "earnings_date", "eps_estimate", "reported_eps", "surprise_pct"]
                if all(c in df.columns for c in needed):
                    e_frames.append(df[needed])
        except Exception as e:
            failures.append((t, "earnings", repr(e)))

        try:
            ud = yf.Ticker(t).upgrades_downgrades
            if ud is not None and not ud.empty:
                ud = ud.reset_index().rename(columns={"GradeDate": "grade_date"})
                ud["ticker"] = t
                ud_frames.append(ud)
        except Exception as e:
            failures.append((t, "ratings", repr(e)))

    if failures:
        print(f"  {len(failures)} ticker/field combos raised an unexpected error (skipped, not fatal):", flush=True)
        for t, kind, err in failures[:20]:
            print(f"    {t} ({kind}): {err}", flush=True)
    pd.concat(e_frames, ignore_index=True).to_parquet(DATA_DIR / "live_earnings.parquet", index=False)
    pd.concat(ud_frames, ignore_index=True).to_parquet(DATA_DIR / "live_ratings.parquet", index=False)


def main():
    print("Refreshing prices...", flush=True)
    tickers = refresh_prices()
    print(f"  {len(tickers)} tickers", flush=True)
    print("Refreshing earnings/ratings (this is the slow part, ~10-20 min for the full universe -- "
          "progress prints every 20 tickers, it is NOT stuck if there's no output for a bit)...", flush=True)
    refresh_earnings_and_ratings(sorted(tickers))
    print("Done.", flush=True)


if __name__ == "__main__":
    main()
