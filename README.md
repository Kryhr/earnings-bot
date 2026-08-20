# Earnings Bet Bot

Discord bot for the validated earnings-bet strategy from `~/earnings-bet-strategy`.
It's a **signal + tracking bot, not an auto-trader**: it tells you what to buy/sell
and when, you execute manually in your own brokerage, and you confirm back to
the bot with `/entered` and `/exited` so it can size the next trade correctly
and track your real returns.

## Setup

1. **Discord application + bot token** (do this at discord.com/developers/applications --
   see the chat for the exact click-through steps). Copy the bot token.
2. Copy `.env.example` to `.env` and fill in:
   - `DISCORD_BOT_TOKEN` -- the token from step 1
   - `DISCORD_CHANNEL_ID` -- right-click the channel in Discord (with Developer
     Mode on in Discord settings) and "Copy Channel ID"
3. Install dependencies:
   ```
   python -m pip install -r requirements.txt
   ```
4. Run once to initialize your starting balance (edit the amount, this only
   needs to run once -- it won't overwrite an existing balance):
   ```
   python -c "from bot import db; db.init_db(starting_balance=100.0)"
   ```
5. Start the bot:
   ```
   python -m bot.main
   ```

The bot will log in, sync its slash commands, and start the daily scan loop
immediately, then every 24 hours after that.

## Commands

- `/balance` -- show current cash + total equity
- `/balance set_to:<amount>` -- override the tracked balance (e.g. after a deposit)
- `/entered ticker:<TICKER> price:<price>` -- confirm you bought; sizes automatically
  from current equity unless you pass `dollars:<amount>` to override
- `/exited ticker:<TICKER> price:<price>` -- confirm you sold; records PnL and updates balance
- `/positions` -- list open positions
- `/history limit:<n>` -- recent closed trades + win rate + total PnL
- `/scan` -- manually trigger a signal check right now (in addition to the daily automatic one)

## How the daily job works

Once every 24 hours, the bot:
1. Refreshes price/earnings/analyst-rating data for the 287-ticker universe.
2. Classifies any position you entered but hasn't reacted to its print yet
   (beat path = trailing ATR stop; held path = hold to next earnings).
3. Checks open positions for exit triggers (ATR stop hit, or next earnings imminent).
4. Posts new buy signals for tickers in this quarter's top-150 selection with
   an imminent, qualifying print (beat streak >= 1), sized off your current equity.

## Known live-execution caveats (see full audit in chat)

- Needs a reliable earnings-calendar feed; dates occasionally shift.
- No slippage modeled -- real fills may differ slightly from the alert price.
- Confirm your broker supports fractional shares for every ticker you get signaled.
- There's a real gap between "bot posts a signal" and "you actually place the
  order" -- the daily job is designed to run right after market close so you
  can act at the next open, but it's still manual execution, not instant.
- The strategy carries real single-position tail risk (documented cases of
  60-80% single-trade losses during genuine market crises like COVID) -- this
  is inherent to the "hold to next earnings" design, not a bug.
