"""
Pulls real historical quarterly revenue from SEC EDGAR's free XBRL API.
Ported from earnings-bet-strategy/scripts/pull_edgar_financials.py so the
live bot's selection score has all three factors the backtest validated
(momentum, crash-risk, revenue growth) instead of silently running with
revenue_growth=None for every ticker forever.

EDGAR filings only change quarterly, so unlike prices this does NOT need
a daily pull -- refresh_edgar_revenue() only re-pulls if the cached file
is missing or older than REFRESH_INTERVAL_DAYS, keeping load on SEC's
servers (and this run's wall-clock time) minimal.
"""
import json
import time
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
EDGAR_PATH = DATA_DIR / "edgar_revenue.parquet"
REFRESH_INTERVAL_DAYS = 7

HEADERS = {"User-Agent": "Independent research contact@example.com"}
REVENUE_TAGS = [
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
]


def _fetch_json(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def _load_ticker_to_cik():
    data = _fetch_json("https://www.sec.gov/files/company_tickers.json")
    return {v["ticker"]: str(v["cik_str"]).zfill(10) for v in data.values()}


def _extract_quarterly_revenue(facts):
    """
    Most companies don't file an explicit standalone "3 months ended Dec 31"
    fact -- the 10-K reports the full fiscal year instead, so Q4 has to be
    derived as annual minus the sum of the three explicitly-reported
    quarters. Without this, ~half the universe silently loses one quarter
    out of every four, which breaks any fixed "4 rows back = 1 year"
    comparison.
    """
    for tag in REVENUE_TAGS:
        node = facts.get("facts", {}).get("us-gaap", {}).get(tag)
        if node is None:
            continue
        units = node.get("units", {}).get("USD", [])
        quarterly, annual = [], []
        for u in units:
            if u.get("form") not in ("10-Q", "10-K"):
                continue
            start, end = u.get("start"), u.get("end")
            if not start or not end:
                continue
            days = (pd.Timestamp(end) - pd.Timestamp(start)).days
            if 80 <= days <= 100:
                quarterly.append({"period_end": end, "revenue": u["val"], "filed": u.get("filed")})
            elif 350 <= days <= 380:
                annual.append({"period_end": end, "revenue": u["val"], "filed": u.get("filed")})
        if not quarterly and not annual:
            continue

        q_df = pd.DataFrame(quarterly).drop_duplicates(subset=["period_end"], keep="last") if quarterly else pd.DataFrame(columns=["period_end", "revenue", "filed"])
        a_df = pd.DataFrame(annual).drop_duplicates(subset=["period_end"], keep="last") if annual else pd.DataFrame(columns=["period_end", "revenue", "filed"])

        derived = []
        for a_row in a_df.itertuples():
            fy_end = pd.Timestamp(a_row.period_end)
            fy_start = fy_end - pd.Timedelta(days=365)
            same_year_q = q_df[(pd.to_datetime(q_df["period_end"]) > fy_start) & (pd.to_datetime(q_df["period_end"]) < fy_end)]
            if len(same_year_q) == 3 and a_row.period_end not in set(q_df["period_end"]):
                q4_revenue = a_row.revenue - same_year_q["revenue"].sum()
                derived.append({"period_end": a_row.period_end, "revenue": q4_revenue, "filed": a_row.filed})

        combined = pd.concat([q_df, pd.DataFrame(derived)], ignore_index=True) if derived else q_df
        if not combined.empty:
            return combined.sort_values("period_end").drop_duplicates(subset=["period_end"], keep="last")
    return None


def _pull(tickers):
    print(f"  Looking up CIKs for {len(tickers)} tickers...", flush=True)
    t2c = _load_ticker_to_cik()

    frames, no_cik, no_rev = [], [], []
    for i, t in enumerate(tickers):
        if i % 50 == 0:
            print(f"  ...{i}/{len(tickers)}", flush=True)
        cik = t2c.get(t)
        if cik is None:
            no_cik.append(t)
            continue
        try:
            facts = _fetch_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json")
        except Exception:
            no_rev.append(t)
            time.sleep(0.15)
            continue
        rev = _extract_quarterly_revenue(facts)
        if rev is None or rev.empty:
            no_rev.append(t)
        else:
            rev["ticker"] = t
            frames.append(rev)
        time.sleep(0.12)  # stay well under SEC's fair-use rate guidance

    if no_cik:
        print(f"  no CIK found for {len(no_cik)} tickers: {no_cik[:15]}{'...' if len(no_cik) > 15 else ''}", flush=True)
    if no_rev:
        print(f"  no usable revenue tag for {len(no_rev)} tickers: {no_rev[:15]}{'...' if len(no_rev) > 15 else ''}", flush=True)

    if not frames:
        return None
    out = pd.concat(frames, ignore_index=True)
    out["period_end"] = pd.to_datetime(out["period_end"])
    return out


def refresh_edgar_revenue(tickers, force=False):
    """
    Pulls SEC EDGAR quarterly revenue for `tickers` and writes
    data/edgar_revenue.parquet, unless a cached copy already exists and
    is younger than REFRESH_INTERVAL_DAYS (skip -- EDGAR filings don't
    change daily, no reason to hit SEC's API and burn several minutes
    every run). Returns True if a fresh pull happened, False if skipped.
    """
    if not force and EDGAR_PATH.exists():
        age_days = (time.time() - EDGAR_PATH.stat().st_mtime) / 86400
        if age_days < REFRESH_INTERVAL_DAYS:
            print(f"  edgar_revenue.parquet is {age_days:.1f}d old (< {REFRESH_INTERVAL_DAYS}d) -- skipping pull", flush=True)
            return False

    out = _pull(tickers)
    if out is None or out.empty:
        print("  WARNING: EDGAR pull returned 0 usable rows -- keeping any previous edgar_revenue.parquet untouched.", flush=True)
        return False
    out.to_parquet(EDGAR_PATH, index=False)
    print(f"  wrote {len(out):,} rows, {out['ticker'].nunique()} tickers to {EDGAR_PATH.name}", flush=True)
    return True
