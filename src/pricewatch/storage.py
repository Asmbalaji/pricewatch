"""SQLite persistence for alert rules and price history."""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

CONDITIONS = ("above", "below", "pct_change")


@dataclass
class Alert:
    id: int
    coin: str
    vs: str
    condition: str
    threshold: float
    active: bool
    created_at: float
    last_triggered: Optional[float]


SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    coin TEXT NOT NULL,
    vs TEXT NOT NULL DEFAULT 'usd',
    condition TEXT NOT NULL,
    threshold REAL NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    last_triggered REAL
);
CREATE TABLE IF NOT EXISTS prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    coin TEXT NOT NULL,
    vs TEXT NOT NULL,
    price REAL NOT NULL,
    ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_prices_coin_ts ON prices (coin, vs, ts);
"""


def _row_to_alert(row: sqlite3.Row) -> Alert:
    return Alert(
        id=row["id"],
        coin=row["coin"],
        vs=row["vs"],
        condition=row["condition"],
        threshold=row["threshold"],
        active=bool(row["active"]),
        created_at=row["created_at"],
        last_triggered=row["last_triggered"],
    )


class Store:
    def __init__(self, path: str = ":memory:") -> None:
        if path != ":memory:":
            p = Path(path).expanduser()
            p.parent.mkdir(parents=True, exist_ok=True)
            path = str(p)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ---- alerts -------------------------------------------------------
    def add_alert(self, coin: str, condition: str, threshold: float, vs: str = "usd") -> int:
        if condition not in CONDITIONS:
            raise ValueError(f"condition must be one of {CONDITIONS}")
        if threshold <= 0:
            raise ValueError("threshold must be positive")
        cur = self.conn.execute(
            "INSERT INTO alerts (coin, vs, condition, threshold, created_at) VALUES (?,?,?,?,?)",
            (coin.lower(), vs.lower(), condition, threshold, time.time()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def list_alerts(self, active_only: bool = False) -> List[Alert]:
        sql = "SELECT * FROM alerts"
        if active_only:
            sql += " WHERE active = 1"
        sql += " ORDER BY id"
        return [_row_to_alert(r) for r in self.conn.execute(sql)]

    def remove_alert(self, alert_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM alerts WHERE id = ?", (alert_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def mark_triggered(self, alert_id: int, ts: Optional[float] = None) -> None:
        self.conn.execute(
            "UPDATE alerts SET last_triggered = ? WHERE id = ?", (ts or time.time(), alert_id)
        )
        self.conn.commit()

    # ---- prices -------------------------------------------------------
    def record_price(self, coin: str, vs: str, price: float, ts: Optional[float] = None) -> None:
        self.conn.execute(
            "INSERT INTO prices (coin, vs, price, ts) VALUES (?,?,?,?)",
            (coin, vs, price, ts or time.time()),
        )
        self.conn.commit()

    def previous_price(self, coin: str, vs: str) -> Optional[float]:
        row = self.conn.execute(
            "SELECT price FROM prices WHERE coin=? AND vs=? ORDER BY ts DESC, id DESC LIMIT 1",
            (coin, vs),
        ).fetchone()
        return row["price"] if row else None

    def history(self, coin: str, vs: str = "usd", limit: int = 20) -> List[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM prices WHERE coin=? AND vs=? ORDER BY ts DESC, id DESC LIMIT ?",
                (coin.lower(), vs.lower(), limit),
            )
        )

    def price_stats(self):
        """Per (coin, vs): sample count, min, max, average, latest price."""
        return list(
            self.conn.execute(
                """
                SELECT p.coin, p.vs, COUNT(*) AS n, MIN(p.price) AS lo, MAX(p.price) AS hi,
                       AVG(p.price) AS avg,
                       (SELECT price FROM prices q WHERE q.coin=p.coin AND q.vs=p.vs
                        ORDER BY ts DESC, id DESC LIMIT 1) AS last
                FROM prices p GROUP BY p.coin, p.vs ORDER BY p.coin
                """
            )
        )
