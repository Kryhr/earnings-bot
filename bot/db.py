"""
Simple SQLite state store: account balance, open positions, closed trade
history. This is the source of truth the bot uses to size new signals and
compute returns -- it only changes when the user confirms an action via a
slash command (/entered, /exited, /balance set), never automatically,
since the bot tells the user what to do but doesn't execute trades itself.
"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS account (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    balance REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS positions (
    ticker TEXT PRIMARY KEY,
    entry_date TEXT NOT NULL,
    entry_price REAL NOT NULL,
    dollars REAL NOT NULL,
    shares REAL NOT NULL,
    stop_price REAL,
    peak_price REAL,
    path TEXT,              -- 'beat' (trailing ATR stop) or 'held' (hold to next earnings)
    next_earnings_date TEXT  -- known/estimated next report date, for the 'held' path
);

CREATE TABLE IF NOT EXISTS trade_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    entry_date TEXT NOT NULL,
    entry_price REAL NOT NULL,
    exit_date TEXT NOT NULL,
    exit_price REAL NOT NULL,
    dollars REAL NOT NULL,
    pnl_dollars REAL NOT NULL,
    ret_pct REAL NOT NULL
);
"""


@contextmanager
def _conn():
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(starting_balance=None):
    with _conn() as conn:
        conn.executescript(SCHEMA)
        if starting_balance is not None:
            conn.execute(
                "INSERT INTO account (id, balance) VALUES (1, ?) "
                "ON CONFLICT(id) DO NOTHING", (starting_balance,)
            )


def get_balance():
    with _conn() as conn:
        row = conn.execute("SELECT balance FROM account WHERE id=1").fetchone()
        return row["balance"] if row else None


def set_balance(amount):
    with _conn() as conn:
        conn.execute(
            "INSERT INTO account (id, balance) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET balance=excluded.balance", (amount,)
        )


def open_position(ticker, entry_price, dollars, stop_price=None, path=None, next_earnings_date=None):
    shares = dollars / entry_price
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO positions "
            "(ticker, entry_date, entry_price, dollars, shares, stop_price, peak_price, path, next_earnings_date) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ticker, datetime.now(timezone.utc).isoformat(), entry_price, dollars, shares,
             stop_price, entry_price, path, next_earnings_date),
        )
        row = conn.execute("SELECT balance FROM account WHERE id=1").fetchone()
        bal = row["balance"] if row else 0.0
        conn.execute(
            "INSERT INTO account (id, balance) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET balance=excluded.balance", (bal - dollars,)
        )


def update_peak_price(ticker, new_peak):
    with _conn() as conn:
        conn.execute("UPDATE positions SET peak_price=? WHERE ticker=?", (new_peak, ticker))


def close_position(ticker, exit_price):
    with _conn() as conn:
        row = conn.execute("SELECT * FROM positions WHERE ticker=?", (ticker,)).fetchone()
        if row is None:
            return None
        proceeds = row["shares"] * exit_price
        pnl = proceeds - row["dollars"]
        ret_pct = exit_price / row["entry_price"] - 1
        conn.execute(
            "INSERT INTO trade_history (ticker, entry_date, entry_price, exit_date, exit_price, dollars, pnl_dollars, ret_pct) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (ticker, row["entry_date"], row["entry_price"], datetime.now(timezone.utc).isoformat(),
             exit_price, row["dollars"], pnl, ret_pct),
        )
        conn.execute("DELETE FROM positions WHERE ticker=?", (ticker,))
        bal_row = conn.execute("SELECT balance FROM account WHERE id=1").fetchone()
        bal = bal_row["balance"] if bal_row else 0.0
        conn.execute(
            "INSERT INTO account (id, balance) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET balance=excluded.balance", (bal + proceeds,)
        )
        return {"pnl": pnl, "ret_pct": ret_pct, "proceeds": proceeds}


def list_positions():
    with _conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM positions").fetchall()]


def list_history(limit=25):
    with _conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM trade_history ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()]


def equity():
    """cash + cost-basis of open positions (matches the backtest's equity_now definition)."""
    bal = get_balance() or 0.0
    positions = list_positions()
    return bal + sum(p["dollars"] for p in positions)
