"""
One-shot health check for a fresh machine. Run with:
    python -m bot.diagnose
Checks (in order, each independent so one failure doesn't hide the rest):
  1. yfinance import + version
  2. a single-ticker pull (AAPL) -- the real test of Yahoo connectivity;
     a raw network ping to the bare domain isn't reliable here since
     Yahoo blocks generic requests without a browser-like User-Agent,
     which would produce a false failure even when yfinance itself works
  3. a small batch pull (5 tickers) -- batch mode is what refresh_data.py
     actually uses, and can fail differently than a single-ticker pull
  4. .env loaded correctly (token/channel ID present, no hidden chars)
  5. the Discord token actually authenticates against Discord's API
Prints a clear PASS/FAIL per check plus the real error text, so a report
back to me is just "line 3 failed, here's what it printed" instead of a
back-and-forth of one-off commands.
"""
import sys


def check_yfinance_version():
    try:
        import yfinance
        return True, f"yfinance {yfinance.__version__}"
    except Exception as e:
        return False, repr(e)


def check_single_ticker():
    try:
        import yfinance as yf
        df = yf.download("AAPL", period="5d", progress=False, timeout=15)
        if df is None or df.empty:
            return False, "download returned empty (no error raised, just no data)"
        return True, f"{len(df)} rows, last close {float(df['Close'].iloc[-1].item() if hasattr(df['Close'].iloc[-1], 'item') else df['Close'].iloc[-1]):.2f}"
    except Exception as e:
        return False, repr(e)


def check_batch_pull():
    try:
        import yfinance as yf
        tickers = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA"]
        data = yf.download(tickers, period="5d", group_by="ticker", progress=False, threads=True, timeout=15)
        ok = [t for t in tickers if t in data.columns.get_level_values(0) and not data[t].dropna(how="all").empty]
        if not ok:
            return False, "all 5 test tickers came back empty"
        return True, f"{len(ok)}/5 tickers OK: {ok}"
    except Exception as e:
        return False, repr(e)


def check_env():
    try:
        from . import config
        if not config.DISCORD_BOT_TOKEN:
            return False, f"no token loaded (looked for .env at {config.ENV_PATH}, found: {config.loaded})"
        if not config.DISCORD_CHANNEL_ID:
            return False, "token found but no channel ID"
        if "paste_your" in config.DISCORD_BOT_TOKEN or "paste_the" in str(config.DISCORD_CHANNEL_ID):
            return False, ".env still has the placeholder text, not real values"
        return True, f"token length {len(config.DISCORD_BOT_TOKEN)}, channel id {config.DISCORD_CHANNEL_ID}"
    except Exception as e:
        return False, repr(e)


def check_discord_token():
    try:
        import requests
        from . import config
        if not config.DISCORD_BOT_TOKEN:
            return False, "skipped -- no token loaded (see previous check)"
        resp = requests.get(
            "https://discord.com/api/v10/users/@me",
            headers={"Authorization": f"Bot {config.DISCORD_BOT_TOKEN}"},
            timeout=10,
        )
        if resp.status_code == 200:
            name = resp.json().get("username", "?")
            return True, f"authenticated as {name}"
        return False, f"HTTP {resp.status_code}: {resp.text[:150]}"
    except Exception as e:
        return False, repr(e)


CHECKS = [
    ("yfinance import", check_yfinance_version),
    ("single-ticker pull (AAPL)", check_single_ticker),
    ("batch pull (5 tickers)", check_batch_pull),
    (".env loaded correctly", check_env),
    ("Discord token authenticates", check_discord_token),
]


def main():
    print("=" * 60)
    all_ok = True
    for name, fn in CHECKS:
        ok, detail = fn()
        all_ok = all_ok and ok
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {name}: {detail}")
    print("=" * 60)
    print("ALL CHECKS PASSED" if all_ok else "SOME CHECKS FAILED -- see FAIL lines above")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
