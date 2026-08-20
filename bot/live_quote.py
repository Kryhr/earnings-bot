"""
Near-real-time price lookup, for use only when the daily job runs before
today's close (so the daily OHLC parquet still only has yesterday's bar).
Never used for the selection/beat-streak logic itself -- that stays on
completed-day data, exactly like the backtest.
"""
import yfinance as yf


def get_live_price(ticker):
    try:
        df = yf.download(ticker, period="1d", interval="1m", progress=False)
        if not df.empty:
            return float(df["Close"].iloc[-1].item() if hasattr(df["Close"].iloc[-1], "item") else df["Close"].iloc[-1])
    except Exception:
        pass
    try:
        info = yf.Ticker(ticker).info
        price = info.get("regularMarketPrice") or info.get("currentPrice")
        if price is not None:
            return float(price)
    except Exception:
        pass
    return None
