"""Speicher: alles liegt in einer SQLite-Datei unter data/, damit nichts beim Neustart verloren geht."""
import json
import sqlite3
import time

from config import DATA_DIR, DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    portfolio TEXT NOT NULL, coin TEXT NOT NULL, side TEXT NOT NULL,
    qty REAL, entry REAL, exit REAL, max_notional REAL,
    pnl REAL, fees REAL, funding REAL, net REAL,
    opened_at REAL, closed_at REAL, reason TEXT, supporters TEXT
);
CREATE INDEX IF NOT EXISTS trades_pf ON trades(portfolio, closed_at);
CREATE TABLE IF NOT EXISTS equity (portfolio TEXT NOT NULL, ts REAL NOT NULL, equity REAL NOT NULL);
CREATE INDEX IF NOT EXISTS equity_pf ON equity(portfolio, ts);
CREATE TABLE IF NOT EXISTS log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, level TEXT, msg TEXT);
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, kind TEXT, title TEXT, detail TEXT);
"""


class Storage:
    def __init__(self, path=DB_PATH):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.commit()

    # ---------- key/value ----------
    def get(self, key, default=None):
        row = self.db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def put(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO kv(key, value) VALUES(?, ?)", (key, json.dumps(value)))
        self.db.commit()

    # ---------- trades ----------
    def add_trade(self, t):
        self.db.execute(
            """INSERT INTO trades(portfolio, coin, side, qty, entry, exit, max_notional, pnl, fees,
               funding, net, opened_at, closed_at, reason, supporters)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (t["portfolio"], t["coin"], t["side"], t["qty"], t["entry"], t["exit"], t["max_notional"],
             t["pnl"], t["fees"], t["funding"], t["net"], t["opened_at"], t["closed_at"], t["reason"],
             json.dumps(t.get("supporters", {}))),
        )
        self.db.commit()

    def trades(self, portfolio, limit=100, since=0):
        rows = self.db.execute(
            "SELECT * FROM trades WHERE portfolio=? AND closed_at>=? ORDER BY closed_at DESC LIMIT ?",
            (portfolio, since, limit),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["supporters"] = json.loads(d["supporters"] or "{}")
            out.append(d)
        return out

    def trade_stats(self, portfolio, since=0):
        r = self.db.execute(
            """SELECT COUNT(*) n, COALESCE(SUM(net),0) net, COALESCE(SUM(CASE WHEN net>0 THEN 1 ELSE 0 END),0) wins
               FROM trades WHERE portfolio=? AND closed_at>=?""",
            (portfolio, since),
        ).fetchone()
        return {"n": r["n"], "net": r["net"], "wins": r["wins"]}

    def coin_stats(self, portfolio, since=0):
        rows = self.db.execute(
            """SELECT coin, COUNT(*) n, SUM(net) net, SUM(CASE WHEN net>0 THEN 1 ELSE 0 END) wins
               FROM trades WHERE portfolio=? AND closed_at>=? GROUP BY coin ORDER BY net DESC""",
            (portfolio, since),
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_portfolio(self, portfolio):
        self.db.execute("DELETE FROM trades WHERE portfolio=?", (portfolio,))
        self.db.execute("DELETE FROM equity WHERE portfolio=?", (portfolio,))
        self.db.commit()

    # ---------- equity curve ----------
    def add_equity(self, portfolio, equity, ts=None):
        self.db.execute("INSERT INTO equity(portfolio, ts, equity) VALUES(?,?,?)",
                        (portfolio, ts or time.time(), equity))
        self.db.commit()

    def equity_curve(self, portfolio, since=0, max_points=400):
        rows = self.db.execute(
            "SELECT ts, equity FROM equity WHERE portfolio=? AND ts>=? ORDER BY ts", (portfolio, since)
        ).fetchall()
        pts = [(r["ts"], r["equity"]) for r in rows]
        if len(pts) > max_points:
            step = len(pts) / max_points
            pts = [pts[int(i * step)] for i in range(max_points)] + [pts[-1]]
        return pts

    def equity_at(self, portfolio, ts):
        r = self.db.execute(
            "SELECT equity FROM equity WHERE portfolio=? AND ts<=? ORDER BY ts DESC LIMIT 1", (portfolio, ts)
        ).fetchone()
        return r["equity"] if r else None

    # ---------- log & events ----------
    def log(self, msg, level="info"):
        self.db.execute("INSERT INTO log(ts, level, msg) VALUES(?,?,?)", (time.time(), level, msg))
        self.db.execute("DELETE FROM log WHERE id < (SELECT MAX(id) - 3000 FROM log)")
        self.db.commit()

    def recent_log(self, limit=60):
        return [dict(r) for r in self.db.execute(
            "SELECT ts, level, msg FROM log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]

    def event(self, kind, title, detail=""):
        self.db.execute("INSERT INTO events(ts, kind, title, detail) VALUES(?,?,?,?)",
                        (time.time(), kind, title, detail))
        self.db.commit()

    def recent_events(self, limit=40):
        return [dict(r) for r in self.db.execute(
            "SELECT ts, kind, title, detail FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]

    def wipe(self, keep_learning=True):
        keep = {}
        if keep_learning:
            for k in ("trust", "traders"):
                v = self.get(k)
                if v is not None:
                    keep[k] = v
        for t in ("kv", "trades", "equity", "log", "events"):
            self.db.execute(f"DELETE FROM {t}")
        self.db.commit()
        for k, v in keep.items():
            self.put(k, v)
