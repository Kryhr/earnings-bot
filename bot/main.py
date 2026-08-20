"""
Discord bot entry point. Slash commands let you tell the bot when you've
entered/exited a trade and check balance/positions/history; a daily
scheduled task (fixed at 3:30 PM ET -- 30 min before the 4:00 PM close,
not "24h from whenever the bot started") refreshes data, classifies
newly-entered positions into beat/held paths, checks exit conditions, and
posts new buy signals -- timed so there's still a window to actually place
the order at/near that day's close, matching how the backtest assumes
entries happen (before an AMC report drops, or the day before a BMO one).
"""
import asyncio
from datetime import datetime, time as dtime, timezone
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import tasks

ALERT_TIME_ET = dtime(hour=15, minute=30, tzinfo=ZoneInfo("America/New_York"))

from . import config, db, exit_engine, refresh_data, signal_engine

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)


async def get_channel():
    return await client.fetch_channel(int(config.DISCORD_CHANNEL_ID))


@tree.command(name="balance", description="Show or set your tracked account balance")
@app_commands.describe(set_to="If given, sets the balance to this amount instead of showing it")
async def balance_cmd(interaction: discord.Interaction, set_to: float = None):
    if set_to is not None:
        db.set_balance(set_to)
        await interaction.response.send_message(f"Balance set to **${set_to:,.2f}**.")
    else:
        bal = db.get_balance()
        eq = db.equity()
        await interaction.response.send_message(f"Cash: **${bal:,.2f}**  |  Total equity (cash + open positions): **${eq:,.2f}**")


@tree.command(name="entered", description="Confirm you entered a trade")
@app_commands.describe(ticker="Ticker symbol", price="Price you got filled at", dollars="Optional: override the recommended dollar amount")
async def entered_cmd(interaction: discord.Interaction, ticker: str, price: float, dollars: float = None):
    ticker = ticker.upper()
    if dollars is None:
        eq = db.equity()
        dollars = min(eq / config.TARGET_SLOTS, db.get_balance() or 0.0)
    cash_before = db.get_balance() or 0.0
    db.open_position(ticker, price, dollars)
    warning = ""
    if dollars > cash_before:
        warning = f"\n⚠️ This was larger than your tracked cash (${cash_before:,.2f}) -- balance is now negative, double check `/balance`."
    await interaction.response.send_message(
        f"Recorded: **{ticker}** entered at ${price:.2f} for **${dollars:,.2f}** "
        f"({dollars/price:.4f} shares). Path (beat/held) will be classified once the report reacts.{warning}"
    )


@tree.command(name="exited", description="Confirm you exited a trade")
@app_commands.describe(ticker="Ticker symbol", price="Price you got filled at")
async def exited_cmd(interaction: discord.Interaction, ticker: str, price: float):
    ticker = ticker.upper()
    result = db.close_position(ticker, price)
    if result is None:
        await interaction.response.send_message(f"No open position found for {ticker}.")
        return
    sign = "+" if result["pnl"] >= 0 else ""
    await interaction.response.send_message(
        f"Closed **{ticker}** at ${price:.2f}. PnL: **{sign}${result['pnl']:,.2f}** ({result['ret_pct']:+.2%}). "
        f"New balance: **${db.get_balance():,.2f}**"
    )


@tree.command(name="positions", description="List currently open positions")
async def positions_cmd(interaction: discord.Interaction):
    positions = db.list_positions()
    if not positions:
        await interaction.response.send_message("No open positions.")
        return
    lines = []
    for p in positions:
        path = p["path"] or "pending classification"
        lines.append(f"**{p['ticker']}** entered ${p['entry_price']:.2f} for ${p['dollars']:,.2f} ({path})")
    await interaction.response.send_message("\n".join(lines))


@tree.command(name="history", description="Show recent closed trades")
@app_commands.describe(limit="How many recent trades to show (default 10)")
async def history_cmd(interaction: discord.Interaction, limit: int = 10):
    trades = db.list_history(limit)
    if not trades:
        await interaction.response.send_message("No closed trades yet.")
        return
    lines = [f"{t['ticker']}: {t['ret_pct']:+.2%} (${t['pnl_dollars']:+,.2f})" for t in trades]
    total_pnl = sum(t["pnl_dollars"] for t in trades)
    win_rate = sum(1 for t in trades if t["pnl_dollars"] > 0) / len(trades)
    await interaction.response.send_message(
        "\n".join(lines) + f"\n\n**Total PnL (last {len(trades)}): ${total_pnl:+,.2f}, win rate {win_rate:.0%}**"
    )


@tree.command(name="scan", description="Manually trigger a signal scan right now")
async def scan_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    candidates = signal_engine.find_todays_candidates()
    if not candidates:
        await interaction.followup.send("No qualifying signals right now.")
        return
    lines = []
    for c in candidates:
        line = (f"**{c['ticker']}** reports {c['report_date']} | beat streak {c['beat_streak']} | "
                f"last close ${c['last_close']:.2f} | suggested size **${c['recommended_dollars']:,.2f}**")
        if c.get("evict_suggestion"):
            ev = c["evict_suggestion"]
            line += f" (consider selling **{ev['ticker']}** at ~${ev['at_price']:.2f} to free up cash for this)"
        lines.append(line)
    await interaction.followup.send("\n".join(lines))


@tasks.loop(time=ALERT_TIME_ET)
async def daily_job():
    now_et = datetime.now(ZoneInfo("America/New_York"))
    if now_et.weekday() >= 5:  # weekend, markets closed -- nothing to check
        return

    channel = await get_channel()
    await channel.send(f"Running scan (3:30 PM ET, 30 min before close)... ({now_et.isoformat()})")

    refresh_data.main()

    classified = exit_engine.classify_pending_positions()
    for c in classified:
        if c["path"] == "beat":
            await channel.send(f"📈 **{c['ticker']}** popped on the print (${c['reaction_price']:.2f}) -- now on the trailing-stop path.")
        else:
            await channel.send(f"📉 **{c['ticker']}** sold off on the print (${c['reaction_price']:.2f}) -- holding to next earnings ({c.get('next_earnings_date')}).")

    exits = exit_engine.check_exits()
    for e in exits:
        await channel.send(f"🔔 **EXIT {e['ticker']}** -- {e['reason']}. Current price ${e['current_price']:.2f}. Use `/exited` once you've sold.")

    candidates = signal_engine.find_todays_candidates()
    for c in candidates:
        evict_note = ""
        if c.get("evict_suggestion"):
            ev = c["evict_suggestion"]
            evict_note = f" (cash is tight -- consider selling **{ev['ticker']}** at ~${ev['at_price']:.2f} first, use `/exited`)"
        await channel.send(
            f"🟢 **BUY {c['ticker']}** -- reports {c['report_date']}, beat streak {c['beat_streak']}.{evict_note} "
            f"Suggested size: **${c['recommended_dollars']:,.2f}** at ~${c['last_close']:.2f}. Use `/entered` once you've bought."
        )

    if not classified and not exits and not candidates:
        await channel.send("Nothing to do today.")


@client.event
async def on_ready():
    await tree.sync()
    db.init_db()
    print(f"Logged in as {client.user}")
    if not daily_job.is_running():
        daily_job.start()


def main():
    if not config.DISCORD_BOT_TOKEN:
        raise SystemExit("DISCORD_BOT_TOKEN not set -- fill in .env first.")
    client.run(config.DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    main()
