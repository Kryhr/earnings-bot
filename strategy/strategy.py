"""
Pre-earnings beat-streak bet, with a bounded miss-recovery layer.
See docs/STRATEGY.md for the full design and what's deliberately not built
(analyst estimate-revision entry signal, revenue-vs-EPS miss granularity).

VALIDATED DEFAULTS (see docs/STRATEGY.md "v3: validated formula" section
for the full audit trail -- these came from sweeps + in-sample/out-of-
sample checks, not guesses):

Entry: buy right before the print if the company beat estimates in each of
the last BEAT_STREAK_MIN=1 prior quarters (i.e. just the most recent one;
streak computed from strictly prior earnings events only -- causal).
Testing showed streak>=1 beats streak>=3, and beat magnitude doesn't add
anything on top of it.

If the print beats: trailing-peak exit (8% off the post-earnings high),
capped at MAX_HOLD_DAYS.

If it misses: HOLD_MISS_TO_NEXT_EARNINGS=True -- hold straight through to
the day before the ticker's next scheduled print, rather than a fixed
8-week cap or a "recovery case" technical/analyst check. Testing showed
this beats every variant of trying to be clever about which misses to cut
early (the 200sma+analyst discriminator was net harmful vs. just holding).
"""
from dataclasses import dataclass

import pandas as pd

BEAT_STREAK_MIN = 1
MAX_HOLD_DAYS = 40  # ~8 trading weeks
TRAILING_PEAK_DROP_PCT = 0.08
QUICK_CUT_DAYS = 3
SMA_WINDOW = 200
RECOVERY_DECIDER = None  # override hook for testing alternative recovery-case logic;
# only consulted when HOLD_MISS_TO_NEXT_EARNINGS is False
HOLD_MISS_TO_NEXT_EARNINGS = True  # validated best: hold a miss straight through to the
# next print rather than a fixed cap or a recovery/cut discriminator (see docstring above)
BRANCH_ON_PRICE_REACTION = False  # if True, the beat-path/miss-path branch is chosen by
# whether price actually went UP on the reaction day, not by the EPS beat/miss label --
# testing whether "beat but stock sells off" and "miss but stock pops" were being
# bucketed backwards under the EPS-label branch
RECOVERY_EXIT_PCT = None  # if set (e.g. 0.05), a held-to-next-earnings position exits
# early once price closes this % or more above entry, instead of holding unconditionally
# all the way to the next print
RECOVERY_TRAILING_STOP_PCT = None  # semi-combine: once a held position first closes at or
# above entry (breakeven), start trailing a stop from its peak (like the beat path) instead
# of exiting flat at a fixed recovery %. Lets a real winner run further than RECOVERY_EXIT_PCT
# would, while still locking in gains once they show up, rather than an all-or-nothing choice
# between "ride to next earnings" and "cash out at a small fixed recovery."
RECOVERY_TRAILING_ACTIVATE_PCT = 0.0  # how far above entry a held position must climb before
# RECOVERY_TRAILING_STOP_PCT starts tracking -- 0.0 = activate at breakeven (default above);
# a higher value (e.g. 0.15) skips protecting small bounces and only trails once the position
# is already a real winner, aiming to preserve more of the big-winner upside

REGIME_LOOKUP = None  # optional dict[date] -> bool "is_bear" (e.g. SPY below its 200-day SMA).
# Diagnostic on the 2022 drawdown found the exit logic implicitly assumes a normal drifting-
# upward market: trailing stops got hit 39% of the time in 2022 (vs 15-28% other years) because
# stocks popped on the print then bled off for weeks, and "hold to next earnings" actively lost
# money that year (-3.19% mean) because there was nothing to recover back to in a genuine
# months-long decline. These overrides only apply when REGIME_LOOKUP marks the ENTRY DATE as
# bear -- using only information known at the moment of the decision, not lookahead.
BEAR_TRAILING_PEAK_DROP_PCT = None  # tighter trailing stop during a bear regime, if set
BEAR_HOLD_MISS_TO_NEXT_EARNINGS = None  # override hold-miss behavior during a bear regime, if set

ATR_WINDOW = 14  # standard ATR lookback
ATR_MULTIPLIER = None  # if set (e.g. 3.0, the classic "Chandelier Exit" convention), the
# beat-path trailing stop uses peak - ATR_MULTIPLIER*ATR(14) (recomputed each bar from that
# bar's own trailing 14-day OHLC, so it's exactly as computable live day-to-day as it is here
# -- no lookahead) instead of a flat % off the peak. Diagnostic: 2022 blew through the flat 8%
# stop 39% of the time (vs 15-28% other years) purely because volatility was elevated, not
# because the picks were bad -- an ATR stop widens automatically in choppy periods instead of
# needing a blanket bear-regime override that costs return in good years too.


@dataclass
class EarningsTrade:
    ticker: str
    earnings_date: pd.Timestamp
    entry_date: pd.Timestamp
    entry_price: float
    beat_streak: int
    was_beat: bool
    recovery_case: bool = None  # only set for misses
    exit_date: pd.Timestamp = None
    exit_price: float = None
    exit_reason: str = None  # trailing_stop / cap / recovered_breakeven / quick_cut / insufficient_data


def _beat_streaks(earnings_df: pd.DataFrame) -> pd.DataFrame:
    """Add a 'beat_streak' column: consecutive prior beats before this event."""
    earnings_df = earnings_df.sort_values("earnings_date").reset_index(drop=True)
    streaks = []
    streak = 0
    for row in earnings_df.itertuples():
        streaks.append(streak)
        if pd.notna(row.surprise_pct) and row.surprise_pct > 0:
            streak += 1
        else:
            streak = 0
    earnings_df["beat_streak"] = streaks
    return earnings_df


def _trading_day_index(price_df: pd.DataFrame) -> pd.Series:
    """date -> integer position in the sorted daily bars, for offset lookups."""
    return pd.Series(range(len(price_df)), index=price_df["datetime"].dt.date)


def generate_earnings_trades(
    ticker: str,
    earnings_df: pd.DataFrame,
    price_df: pd.DataFrame,
    ud_df: pd.DataFrame,
) -> list[EarningsTrade]:
    earnings_df = _beat_streaks(earnings_df[earnings_df["ticker"] == ticker])
    price_df = price_df[price_df["ticker"] == ticker].sort_values("datetime").reset_index(drop=True)
    if price_df.empty:
        return []

    price_df["date"] = price_df["datetime"].dt.date
    price_df["sma200"] = price_df["close"].rolling(SMA_WINDOW).mean()
    if ATR_MULTIPLIER is not None:
        prev_close = price_df["close"].shift(1)
        true_range = pd.concat([
            price_df["high"] - price_df["low"],
            (price_df["high"] - prev_close).abs(),
            (price_df["low"] - prev_close).abs(),
        ], axis=1).max(axis=1)
        price_df["atr"] = true_range.rolling(ATR_WINDOW).mean()
    date_to_idx = {d: i for i, d in enumerate(price_df["date"])}
    dates_sorted = price_df["date"].tolist()
    ud = ud_df[ud_df["ticker"] == ticker].copy()
    ud["grade_date"] = pd.to_datetime(ud["grade_date"]).dt.date

    earnings_rows = list(earnings_df.itertuples())

    def _entry_and_reaction_idx(earnings_date):
        report_date = earnings_date.date()
        is_amc = earnings_date.hour >= 12
        if is_amc:
            if report_date not in date_to_idx:
                return None, None
            entry_idx = date_to_idx[report_date]
            reaction_idx = entry_idx + 1
        else:
            later_or_eq = [d for d in dates_sorted if d >= report_date]
            if not later_or_eq:
                return None, None
            reaction_date = later_or_eq[0]
            reaction_idx = date_to_idx[reaction_date]
            entry_idx = reaction_idx - 1
        if entry_idx is None or entry_idx < 0 or reaction_idx >= len(price_df):
            return None, None
        return entry_idx, reaction_idx

    trades = []
    for row_i, row in enumerate(earnings_rows):
        if row.beat_streak < BEAT_STREAK_MIN:
            continue
        if pd.isna(row.earnings_date):
            continue

        report_date = row.earnings_date.date()
        is_amc = row.earnings_date.hour >= 12

        if is_amc:
            if report_date not in date_to_idx:
                continue
            entry_idx = date_to_idx[report_date]
            reaction_idx = entry_idx + 1
        else:
            # BMO: need the trading day strictly before report_date
            later_or_eq = [d for d in dates_sorted if d >= report_date]
            if not later_or_eq:
                continue
            reaction_date = later_or_eq[0]
            reaction_idx = date_to_idx[reaction_date]
            entry_idx = reaction_idx - 1

        if entry_idx < 0 or reaction_idx >= len(price_df):
            continue

        entry_price = price_df["close"].iloc[entry_idx]
        entry_date = price_df["datetime"].iloc[entry_idx]
        was_beat = pd.notna(row.surprise_pct) and row.surprise_pct > 0

        trade = EarningsTrade(
            ticker=ticker,
            earnings_date=row.earnings_date,
            entry_date=entry_date,
            entry_price=entry_price,
            beat_streak=row.beat_streak,
            was_beat=was_beat,
        )

        cap_idx = min(reaction_idx + MAX_HOLD_DAYS, len(price_df) - 1)

        is_bear = REGIME_LOOKUP.get(entry_date.date()) if REGIME_LOOKUP is not None else False
        trailing_drop_pct = BEAR_TRAILING_PEAK_DROP_PCT if (is_bear and BEAR_TRAILING_PEAK_DROP_PCT is not None) else TRAILING_PEAK_DROP_PCT
        hold_miss_to_next = BEAR_HOLD_MISS_TO_NEXT_EARNINGS if (is_bear and BEAR_HOLD_MISS_TO_NEXT_EARNINGS is not None) else HOLD_MISS_TO_NEXT_EARNINGS

        if BRANCH_ON_PRICE_REACTION:
            take_beat_path = price_df["close"].iloc[reaction_idx] >= entry_price
        else:
            take_beat_path = was_beat

        if take_beat_path:
            peak = entry_price
            exit_idx, exit_reason = cap_idx, "cap"
            for j in range(reaction_idx, cap_idx + 1):
                close = price_df["close"].iloc[j]
                peak = max(peak, close)
                if ATR_MULTIPLIER is not None:
                    atr_j = price_df["atr"].iloc[j]
                    stop_level = peak - ATR_MULTIPLIER * atr_j if pd.notna(atr_j) else peak * (1 - trailing_drop_pct)
                else:
                    stop_level = peak * (1 - trailing_drop_pct)
                if close <= stop_level:
                    exit_idx, exit_reason = j, "trailing_stop"
                    break
            trade.exit_date = price_df["datetime"].iloc[exit_idx]
            trade.exit_price = price_df["close"].iloc[exit_idx]
            trade.exit_reason = exit_reason
        elif hold_miss_to_next:
            next_earnings_idx = None
            for next_row in earnings_rows[row_i + 1:]:
                if pd.isna(next_row.earnings_date):
                    continue
                next_entry_idx, _ = _entry_and_reaction_idx(next_row.earnings_date)
                if next_entry_idx is not None:
                    next_earnings_idx = next_entry_idx
                    break
            if next_earnings_idx is None or next_earnings_idx <= reaction_idx:
                exit_idx, exit_reason = len(price_df) - 1, "held_to_data_end"
            else:
                exit_idx, exit_reason = next_earnings_idx, "held_to_next_earnings"

            if RECOVERY_TRAILING_STOP_PCT is not None:
                activation_level = entry_price * (1 + RECOVERY_TRAILING_ACTIVATE_PCT)
                activated = False
                peak = entry_price
                for j in range(reaction_idx, exit_idx + 1):
                    close = price_df["close"].iloc[j]
                    if not activated:
                        if close >= activation_level:
                            activated = True
                            peak = close
                        continue
                    peak = max(peak, close)
                    if close <= peak * (1 - RECOVERY_TRAILING_STOP_PCT):
                        exit_idx, exit_reason = j, "recovery_trailing_stop"
                        break
            elif RECOVERY_EXIT_PCT is not None:
                recovery_level = entry_price * (1 + RECOVERY_EXIT_PCT)
                for j in range(reaction_idx, exit_idx + 1):
                    if price_df["close"].iloc[j] >= recovery_level:
                        exit_idx, exit_reason = j, "recovery_exit"
                        break

            trade.exit_reason = exit_reason
            trade.exit_date = price_df["datetime"].iloc[exit_idx]
            trade.exit_price = price_df["close"].iloc[exit_idx]
        else:
            sma_at_miss = price_df["sma200"].iloc[reaction_idx]
            if pd.isna(sma_at_miss):
                trade.exit_reason = "insufficient_data"
                trade.exit_date = entry_date
                trade.exit_price = entry_price
                trades.append(trade)
                continue
            above_trend = price_df["close"].iloc[reaction_idx] >= sma_at_miss

            # same-day only -- using days AFTER the decision point would be a real lookahead
            # leak (we can't know how analysts will react over the next few days before
            # deciding whether to hold or cut). Same-day analyst notes (common right after
            # a print, especially overnight on AMC reports) are legitimately knowable now.
            window = ud[ud["grade_date"] == dates_sorted[reaction_idx]]
            window = window[window["priorPriceTarget"] > 0]
            if len(window):
                net_pt_change = (window["currentPriceTarget"] - window["priorPriceTarget"]).mean()
                analyst_favorable = net_pt_change >= 0
            else:
                analyst_favorable = True  # no data -- don't let a missing signal force a cut

            if RECOVERY_DECIDER is not None:
                recovery_case = RECOVERY_DECIDER(above_trend, analyst_favorable)
            else:
                recovery_case = above_trend and analyst_favorable
            trade.recovery_case = recovery_case

            if recovery_case:
                exit_idx, exit_reason = cap_idx, "cap"
                for j in range(reaction_idx, cap_idx + 1):
                    if price_df["close"].iloc[j] >= entry_price:
                        exit_idx, exit_reason = j, "recovered_breakeven"
                        break
                trade.exit_date = price_df["datetime"].iloc[exit_idx]
                trade.exit_price = price_df["close"].iloc[exit_idx]
                trade.exit_reason = exit_reason
            else:
                cut_idx = min(reaction_idx + QUICK_CUT_DAYS, len(price_df) - 1)
                trade.exit_date = price_df["datetime"].iloc[cut_idx]
                trade.exit_price = price_df["close"].iloc[cut_idx]
                trade.exit_reason = "quick_cut"

        trades.append(trade)

    return trades
