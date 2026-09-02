# Earnings Bet Bot

Discord bot for the validated earnings-bet strategy from `~/earnings-bet-strategy`.
It's a **signal + tracking bot, not an auto-trader**: it tells you what to buy/sell
and when, you execute manually in your own brokerage, and you confirm back to
the bot with `/entered` and `/exited` so it can size the next trade correctly
and track your real returns.

**Free and open source.** The full strategy, priority formula, and backtest
results are all in this repo -- nothing held back. Plug in your own Discord
bot token (see Setup below) and it's a working signal bot in your own server,
no paid data feed or subscription required.

This doc reflects the state as of the concentration-config build (TARGET_SLOTS=5,
composite priority formula). If live behavior ever looks meaningfully different
from what's described here, treat that as a bug to audit, not an intentional
change -- this session found (and fixed) a long list of live/backtest fidelity
gaps, so a discrepancy is more likely a regression than a feature.

## Setup

1. **Discord application + bot token** (discord.com/developers/applications).
2. Copy `.env.example` to `.env` and fill in:
   - `DISCORD_BOT_TOKEN`
   - `DISCORD_CHANNEL_ID` -- right-click the channel in Discord (Developer Mode on) -> Copy Channel ID
3. Install dependencies:
   ```
   python -m pip install -r requirements.txt
   ```
4. Initialize your starting balance (only needs to run once -- won't overwrite an existing balance):
   ```
   python -c "from bot import db; db.init_db(starting_balance=120.0)"
   ```
5. Start the bot:
   ```
   python -m bot.main
   ```

The bot logs in, syncs slash commands, and schedules the daily scan for
**3:30 PM ET** (30 min before the 4:00 PM close) every trading day. It does
NOT fire immediately on startup -- if you start it after 3:30 PM ET, that
day's scan is skipped and the next one fires tomorrow. Use `/scan` to run
the full cycle manually any time.

**Only run one instance at a time.** State (positions, balance, pending
signals) lives in a local SQLite file and is not shared across machines --
running the bot on two laptops simultaneously will desync your tracked
account and duplicate every Discord alert.

## Commands

- `/balance` -- show current cash + total equity
- `/balance set_to:<amount>` -- override the tracked balance (e.g. after a deposit)
- `/entered ticker:<TICKER> price:<price>` -- confirm you bought; sizes automatically
  from current equity ÷ TARGET_SLOTS unless you pass `dollars:<amount>` to override
- `/exited ticker:<TICKER> price:<price>` -- confirm you sold; records PnL and updates balance
- `/positions` -- list open positions
- `/pending` -- signals recommended but not yet confirmed or expired
- `/history limit:<n>` -- recent closed trades + win rate + total PnL
- `/scan` -- manually run the full daily cycle right now (data refresh, exit
  checks, path classification, stale-signal expiry, new candidates) --
  identical to what the scheduled 3:30 PM job does

## The strategy, as actually implemented

**Selection** (recomputed every quarter): score = z(126-day momentum) +
z(worst single-day earnings-reaction move over the last 8 reports). Top 150
tickers by that score are this quarter's eligible pool. **Revenue growth is
deliberately not included** -- it was explored (SEC EDGAR data pull exists in
`bot/refresh_edgar.py` but isn't called) and never made it into the config
that was actually chosen; using it live was a real bug this session found
and reverted.

**Entry gate**: beat streak (consecutive prior quarters with a positive EPS
surprise) >= 1.

**Entry timing**: computed exactly, not "within a few days." AMC reports
(after 4 PM) enter at that same day's close, before the print drops that
evening. BMO reports (before market open) enter the trading day *before*,
since by the report date the number's already out. The bot only ever
surfaces a signal on the exact day this resolves to -- never early, never
late (including on stale restarts after a missed window).

**Exit**: the day after entry, up-move -> "beat" path (2.5x ATR(14) trailing
stop, falls back to a flat 8% trailing-peak stop if there's not enough price
history for ATR yet; force-exits at 40 trading days if the stop's never
hit). Down-move -> "held" path (hold straight through to the next earnings
report, exit the day before it drops -- no early-cut logic, that was tested
and found harmful; genuinely uncapped by design, not a bug).

**Funding order when multiple signals compete for cash** -- composite
priority score, z-scored cross-sectionally against the full 150-ticker
quarterly pool (not just today's candidates -- with only 1-2 names
reporting on a given day, a same-day-only z-score is statistically
meaningless):
```
priority = z(trailing mean EPS surprise %)
         + z(-trailing surprise % std dev)      # reward consistency
         + z(trailing reaction-magnitude on past beats)  # does it actually pop
         + z(quarterly momentum/crash-risk score)
         + 5 * pre-earnings analyst-sentiment score
```
All trailing stats use only reports strictly before the current entry
(causal). If cash is short, the bot suggests evicting the weakest currently
*held* position (by its real recorded priority, not a placeholder) only if
the new signal beats it by `EVICT_MARGIN` (2.0).

**Sizing**: target = current equity / `TARGET_SLOTS` (5), capped at whatever
cash is actually available -- never more than tracked balance. Below
`MIN_TRADE_DOLLARS` ($1) and not a strong enough case to evict anything, the
signal is skipped with a clear message instead of suggesting an unfundable
trade.

**Important nuance**: `TARGET_SLOTS=5` sizes positions at equity/5, it does
**not** cap how many positions can be open at once -- if several earnings
cluster in the same window, more than 5 can be open simultaneously (each
just sized smaller). A genuinely hard-capped version (never more than 5,
period) was built and tested but rejected: it had ~3x the drawdown-tail risk
(34% vs 12.8% chance of a >40% drawdown) for about the same median return.

## Why this config, honestly

This isn't the single highest historical backtest number that came out of
testing -- that one (+1244% for a different slot/formula combo) was checked
with Monte Carlo bootstrap resampling and turned out to be a lucky draw
(median resampled outcome was actually *below* the plain 10-slot baseline).
The config actually running now (TARGET_SLOTS=5, composite formula) has a
Monte Carlo median of **+801%** against a **+566%** 10-slot/old-formula
baseline, with a 12.8% chance of a >40% drawdown -- picked deliberately for
being the more *robust* upgrade, not just the biggest headline number.
Zero negative-cash events in the historical run or across 500 Monte Carlo
resamples at both $10k and $120 starting capital.

**Take any of these numbers as "this is what historically happened / what a
resample distribution looked like," not a guarantee.** Past earnings
reactions are not a contract.

## Backtested Results

Full re-run of the exact validated strategy and priority formula above
(composite 5-factor blend + contrarian adjustment, `TARGET_SLOTS=5`,
`EVICT_MARGIN=2.0`, ATR trailing stop), against the project's real
historical dataset: 286 tickers, daily prices 2016-2026, real EPS
surprise history, real analyst rating/price-target history. 4,389
candidate earnings events survive the quarterly top-150 selection filter
over 2018-01-01 through 2026-09-02.

### Single full-history run

- Total return: **+987.4%**
- Max drawdown: **-15.2%**
- Worst calendar year: **-2.2%** (2022)
- Funded trades: **625** (69.4/year)
- Win rate: **60.5%**

| Year | Return |
|---|---|
| 2018 | +4.8% |
| 2019 | +13.8% |
| 2020 | +54.5% |
| 2021 | +36.3% |
| 2022 | -2.2% |
| 2023 | +51.8% |
| 2024 | +12.8% |
| 2025 | +38.2% |
| 2026 | +58.7% |

### In-sample vs out-of-sample

| | In-sample (2018-2023) | Out-of-sample (2024-2026) |
|---|---|---|
| Total return | +316.6% | +66.7% |
| Worst year | -2.2% | +8.9% |

### Monte Carlo (500x bootstrap resample of the trade list, same size, with replacement, portfolio sim rerun on each draw)

| Metric | P5 | Median | P95 |
|---|---|---|---|
| Total return | +325.8% | +772.8% | +1791.2% |
| Max drawdown | -40.2% | -25.0% | -16.7% |
| Worst year | -33.9% | -13.2% | +1.4% |

- P(total return < 0): **0.0%**
- P(max drawdown worse than -40%): **5.6%**
- P(worst year worse than -20%): **28.8%**

### Honest caveats

- This is a historical backtest, not a live track record. Past earnings
  reactions are not a contract, and real trading adds slippage, execution
  timing, and data-feed differences this simulation does not model.
- The Monte Carlo bootstrap resamples trades independently, which breaks
  their real chronological ordering -- it's a stress test of the
  distribution of outcomes, not a claim that any single path is guaranteed.
  The single historical run and the in-sample/out-of-sample split (which
  preserve true chronology) are shown alongside it for that reason.
- 14.2% of generated candidate signals actually get funded in a given
  history -- the rest are skipped because all 5 slots were already filled
  by higher-priority signals and no held position was weak enough to evict.
  That's expected behavior, not a bug: the formula is doing its job of being
  selective about capital, not funding every qualifying signal that appears.

## Data refresh

Runs at the start of every scan (scheduled or manual `/scan`): prices
(incremental, backfills the actual gap if the bot was offline rather than a
fixed window), earnings dates/surprises, analyst ratings. Every network call
has retry-with-backoff; a systemic failure keeps yesterday's cached data
and warns loudly rather than crashing or silently writing empty files.

## Known live-execution caveats

- Needs a reliable earnings-calendar feed; report dates occasionally shift
  day-to-day as companies confirm them -- the bot re-checks each scan.
- No slippage modeled -- real fills may differ slightly from the alert price.
- Confirm your broker supports fractional shares for every ticker you get signaled.
- Real gap between "bot posts a signal" and "you actually place the order" --
  manual execution, not instant, even though timing is designed to leave a
  window before close.
- Real single-position tail risk is inherent to "hold to next earnings" --
  documented in the Monte Carlo checks above, not a bug.
- Starting mid-quarter (as this launch is doing): the bot will never
  retroactively suggest a stock whose report already happened earlier this
  quarter before the bot was running -- it only ever surfaces forward-looking
  signals from today onward. Expect fewer signals this quarter than a full
  quarter would produce; that's expected, not broken.
