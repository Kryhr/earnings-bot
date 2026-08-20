"""
Pulls fresh daily price bars, earnings history, and analyst ratings for the
same 287-ticker universe validated in the backtest. Run this once daily
(the scheduler in bot.py triggers it) so signal/exit checks work off
current data. Uses the vendored candidate list in strategy/ so this repo
is fully self-contained -- no dependency on the backtest repo existing on
whatever machine the bot runs on.
"""
import time
from pathlib import Path

import pandas as pd
import yfinance as yf

from strategy.candidate_universe import CANDIDATE_POOL

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"


def refresh_prices():
    """
    First run ever: pull the full 2 years (needed to compute 126-day
    momentum and multi-report crash-risk history at all). Every run after
    that: only pull the last 5 days (covers weekends/holidays with room
    to spare) and merge into what's already stored, instead of
    re-downloading the same ~500 trading days x 293 tickers from scratch
    every single time -- that was pure waste and unnecessary load on
    Yahoo's servers (and a real contributor to rate-limit risk).
    """
    existing_path = DATA_DIR / "live_prices.parquet"
    existing = None
    if existing_path.exists():
        existing = pd.read_parquet(existing_path)
        existing["datetime"] = pd.to_datetime(existing["datetime"])
        pull_period = "5d"
        print("  incremental update (5d) -- already have prior history", flush=True)
    else:
        pull_period = "2y"
        print("  first run -- pulling full 2y history", flush=True)

    # unlike the per-ticker earnings/ratings calls, this single bulk download covers
    # the entire universe at once -- a transient Yahoo hiccup here with no retry
    # would silently abort the whole day's refresh (prices, earnings, everything
    # downstream), a much bigger fidelity gap than one flaky ticker
    data, err = _fetch_with_retries(lambda: yf.download(
        CANDIDATE_POOL, period=pull_period, interval="1d", group_by="ticker",
        auto_adjust=True, progress=False, threads=True))
    if data is None:
        print(f"  WARNING: bulk price download failed after retries ({err!r}) -- "
              "keeping previous live_prices.parquet untouched.", flush=True)
        return set(existing["ticker"].unique()) if existing is not None else set()
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
    new_data = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
        columns=["ticker", "datetime", "open", "high", "low", "close", "volume"])
    new_data["datetime"] = pd.to_datetime(new_data["datetime"])

    combined = pd.concat([existing, new_data], ignore_index=True) if existing is not None else new_data
    combined = combined.drop_duplicates(subset=["ticker", "datetime"], keep="last")
    cutoff = pd.Timestamp.now(tz=combined["datetime"].dt.tz) - pd.Timedelta(days=730)
    combined = combined[combined["datetime"] >= cutoff].sort_values(["ticker", "datetime"])

    combined.to_parquet(existing_path, index=False)
    return set(combined["ticker"].unique())


def _fetch_with_retries(fn, attempts=3, delay_seconds=2):
    """
    A transient Yahoo hiccup on one attempt shouldn't make a ticker that
    normally works fine (e.g. IBM, used reliably throughout this whole
    project) silently look permanently broken for the day -- that's a
    real fidelity gap vs. the backtest, which assumed complete data.
    Retries a few times with a short pause before giving up for real.
    """
    last_error = None
    for attempt in range(attempts):
        try:
            return fn(), None
        except Exception as e:
            last_error = e
            if attempt < attempts - 1:
                time.sleep(delay_seconds)
    return None, last_error


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
        df, err = _fetch_with_retries(lambda: yf.Ticker(t).get_earnings_dates(limit=28))
        try:
            if df is not None and not df.empty:
                df = df.reset_index().rename(columns={
                    "Earnings Date": "earnings_date", "EPS Estimate": "eps_estimate",
                    "Reported EPS": "reported_eps", "Surprise(%)": "surprise_pct",
                })
                df["ticker"] = t
                needed = ["ticker", "earnings_date", "eps_estimate", "reported_eps", "surprise_pct"]
                if all(c in df.columns for c in needed):
                    e_frames.append(df[needed])
            elif err is not None:
                failures.append((t, "earnings", repr(err)))
        except Exception as e:
            failures.append((t, "earnings", repr(e)))

        ud, err = _fetch_with_retries(lambda: yf.Ticker(t).upgrades_downgrades)
        try:
            if ud is not None and not ud.empty:
                ud = ud.reset_index().rename(columns={"GradeDate": "grade_date"})
                ud["ticker"] = t
                ud_frames.append(ud)
            elif err is not None:
                failures.append((t, "ratings", repr(err)))
        except Exception as e:
            failures.append((t, "ratings", repr(e)))

    if failures:
        print(f"  {len(failures)} ticker/field combos raised an unexpected error (skipped, not fatal):", flush=True)
        for t, kind, err in failures[:20]:
            print(f"    {t} ({kind}): {err}", flush=True)

    # A systemic failure (e.g. a missing dependency like lxml) can leave
    # e_frames/ud_frames completely empty even though individual failures
    # were caught above. Don't let that crash the run or, worse, silently
    # overwrite yesterday's good parquet with an empty one -- keep whatever
    # data already exists on disk and surface a loud warning instead.
    earnings_path = DATA_DIR / "live_earnings.parquet"
    ratings_path = DATA_DIR / "live_ratings.parquet"
    if e_frames:
        pd.concat(e_frames, ignore_index=True).to_parquet(earnings_path, index=False)
    else:
        print("  WARNING: 0 tickers returned earnings data this run -- refresh likely failed "
              "systemically (check the errors above, e.g. a missing dependency). "
              f"Keeping previous {earnings_path.name} untouched.", flush=True)
    if ud_frames:
        pd.concat(ud_frames, ignore_index=True).to_parquet(ratings_path, index=False)
    else:
        print("  WARNING: 0 tickers returned ratings data this run -- refresh likely failed "
              "systemically (check the errors above, e.g. a missing dependency). "
              f"Keeping previous {ratings_path.name} untouched.", flush=True)


def main():
    print("Refreshing prices...", flush=True)
    tickers = refresh_prices()
    print(f"  {len(tickers)} tickers", flush=True)
    print("Refreshing earnings/ratings (this is the slow part, ~10-20 min for the full universe -- "
          "progress prints every 20 tickers, it is NOT stuck if there's no output for a bit)...", flush=True)
    refresh_earnings_and_ratings(sorted(tickers))
    # deliberately not pulling EDGAR revenue here -- it isn't part of the
    # validated live selection formula (see signal_engine.py), so there's no
    # point spending the time or hitting SEC's API for a factor that isn't
    # used. bot/refresh_edgar.py is kept around in case revenue growth gets
    # properly re-validated and added later.
    print("Done.", flush=True)


if __name__ == "__main__":
    main()
