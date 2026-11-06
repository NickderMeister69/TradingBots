"""Spielgeld-Konto: Positionen, Gebühren, Funding, Stop-Loss – alles simuliert, nichts geht an eine Börse."""
import copy
import time

from config import SLIPPAGE, TAKER_FEE


def sign(x):
    return 1 if x > 0 else -1 if x < 0 else 0


class Portfolio:
    def __init__(self, pid, name, params, start_capital, created_at=None):
        self.id = pid
        self.name = name
        self.params = copy.deepcopy(params)
        self.start = start_capital
        self.cash = start_capital
        self.positions = {}          # coin -> dict
        self.cooldown = {}           # coin -> Zeitstempel, bis wann gesperrt
        self.blocked = {}            # coin -> Zeitstempel (vom Lernen gesperrt)
        self.peak = start_capital
        self.created_at = created_at or time.time()
        self.totals = {"realized": 0.0, "fees": 0.0, "funding": 0.0}
        self.day = {"date": None, "equity": start_capital, "realized": 0.0, "fees": 0.0, "funding": 0.0}
        self.note = ""

    # ---------- Werte ----------
    def unrealized(self, market):
        u = 0.0
        for coin, p in self.positions.items():
            px = market.get(coin, {}).get("mark") or p["last_px"]
            u += p["qty"] * (px - p["entry"])
        return u

    def equity(self, market):
        return self.cash + self.unrealized(market)

    def gross(self, market):
        return sum(abs(p["qty"]) * (market.get(c, {}).get("mark") or p["last_px"]) for c, p in self.positions.items())

    def notional(self, coin, market):
        p = self.positions.get(coin)
        if not p:
            return 0.0
        px = market.get(coin, {}).get("mark") or p["last_px"]
        return p["qty"] * px

    # ---------- Kosten ----------
    def apply_funding(self, market, dt_seconds):
        for coin, p in self.positions.items():
            m = market.get(coin)
            if not m:
                continue
            p["last_px"] = m["mark"]
            pay = -p["qty"] * m["mark"] * m["funding"] * (dt_seconds / 3600.0)
            self.cash += pay
            p["funding"] += pay
            self.totals["funding"] += pay

    # ---------- Handeln ----------
    def fill(self, coin, delta_qty, mid, reason, now, supporters=None):
        """Kauft (delta>0) oder verkauft (delta<0). Gibt abgeschlossene Trades zurück."""
        closed = []
        if delta_qty == 0:
            return closed
        price = mid * (1 + SLIPPAGE * sign(delta_qty))
        fee = abs(delta_qty) * price * TAKER_FEE
        self.cash -= fee
        self.totals["fees"] += fee
        p = self.positions.get(coin)

        if p is None:
            self._open(coin, delta_qty, price, fee, now, supporters)
            return closed

        p["fees"] += fee
        if sign(delta_qty) == sign(p["qty"]):              # aufstocken
            new_qty = p["qty"] + delta_qty
            p["entry"] = (p["entry"] * p["qty"] + price * delta_qty) / new_qty
            p["qty"] = new_qty
            p["max_notional"] = max(p["max_notional"], abs(new_qty) * price)
            p["adds"] += 1
            return closed

        # abbauen, schließen oder drehen
        close_qty = -delta_qty if abs(delta_qty) <= abs(p["qty"]) else p["qty"]
        pnl = close_qty * (price - p["entry"])
        self.cash += pnl
        self.totals["realized"] += pnl
        p["realized"] += pnl
        p["qty"] -= close_qty
        if abs(p["qty"]) * price < 1e-6:
            closed.append(self._close_record(coin, p, price, reason, now))
            del self.positions[coin]
            rest = delta_qty + close_qty
            if abs(rest) * price > 1e-6:
                self._open(coin, rest, price, 0.0, now, supporters)
        return closed

    def _open(self, coin, qty, price, fee, now, supporters):
        self.positions[coin] = {
            "qty": qty, "entry": price, "first_entry": price, "opened_at": now, "fees": fee,
            "funding": 0.0, "realized": 0.0, "max_notional": abs(qty) * price, "last_px": price,
            "adds": 0, "supporters": supporters or {}, "side": "long" if qty > 0 else "short",
        }

    def _close_record(self, coin, p, price, reason, now):
        net = p["realized"] - p["fees"] + p["funding"]
        return {
            "portfolio": self.id, "coin": coin, "side": p["side"],
            "qty": p["max_notional"] / max(p["entry"], 1e-12), "entry": p["entry"], "exit": price,
            "max_notional": p["max_notional"], "pnl": p["realized"], "fees": p["fees"], "funding": p["funding"],
            "net": net, "opened_at": p["opened_at"], "closed_at": now, "reason": reason,
            "supporters": p.get("supporters", {}),
        }

    def close(self, coin, market, reason, now):
        p = self.positions.get(coin)
        if not p:
            return []
        mid = market.get(coin, {}).get("mid") or p["last_px"]
        return self.fill(coin, -p["qty"], mid, reason, now)

    def close_all(self, market, reason, now):
        out = []
        for coin in list(self.positions):
            out += self.close(coin, market, reason, now)
        return out

    def check_risk(self, market, now):
        """Stop-Loss und simulierte Liquidation."""
        out = []
        lev = max(1.0, self.params["leverage"])
        for coin in list(self.positions):
            p = self.positions[coin]
            m = market.get(coin)
            if not m:
                continue
            move = (m["mark"] - p["entry"]) / p["entry"] * sign(p["qty"])
            if move <= -0.9 / lev:
                out += self.close(coin, market, "Liquidation (simuliert)", now)
                self.cooldown[coin] = now + self.params["cooldown_min"] * 60
            elif move <= -self.params["stop_loss_pct"]:
                out += self.close(coin, market, "Stop-Loss", now)
                self.cooldown[coin] = now + self.params["cooldown_min"] * 60
        return out

    def rebalance(self, targets, market, now, supporters_by_coin, reasons, allow_new=True):
        """Bringt das Konto auf die Ziel-Positionen. targets: coin -> Ziel-Nominalwert (+ long / - short)."""
        prm = self.params
        out = []
        for coin in sorted(set(targets) | set(self.positions)):
            m = market.get(coin)
            if not m:
                continue
            tgt = targets.get(coin, 0.0)
            cur = self.notional(coin, market)
            mid = m["mid"]
            locked = self.cooldown.get(coin, 0) > now or self.blocked.get(coin, 0) > now
            if (locked or not allow_new) and (sign(tgt) != sign(cur) or abs(tgt) > abs(cur)):
                tgt = 0.0 if sign(tgt) != sign(cur) else cur      # nichts Neues aufmachen, nur halten/abbauen
            if cur != 0 and (tgt == 0 or sign(tgt) != sign(cur)):
                out += self.fill(coin, -self.positions[coin]["qty"], mid, reasons.get(coin, "Profis steigen aus"), now)
                cur = 0.0
            if tgt == 0:
                continue
            diff = tgt - cur
            if abs(diff) < prm["min_order_eur"]:
                continue
            if cur != 0 and abs(diff) < prm["rebalance_band"] * abs(cur):
                continue
            qty = diff / mid
            out += self.fill(coin, qty, mid, reasons.get(coin, "Ziel angepasst"), now, supporters_by_coin.get(coin))
        return out

    # ---------- Tageswechsel ----------
    def roll_day(self, date_str, market):
        if self.day["date"] is None:            # erster Tag: zählt ab Startkapital
            self.day["date"] = date_str
        elif self.day["date"] != date_str:
            self.day = {"date": date_str, "equity": self.equity(market), **{k: v for k, v in self.totals.items()}}

    # ---------- Speichern ----------
    def to_dict(self):
        return {k: getattr(self, k) for k in ("id", "name", "params", "start", "cash", "positions", "cooldown",
                                              "blocked", "peak", "created_at", "totals", "day", "note")}

    @classmethod
    def from_dict(cls, d):
        pf = cls(d["id"], d["name"], d["params"], d["start"], d["created_at"])
        for k in ("cash", "positions", "cooldown", "blocked", "peak", "totals", "day", "note"):
            if k in d:
                setattr(pf, k, d[k])
        return pf
