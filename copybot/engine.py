"""Der Motor: holt Daten, lernt, handelt mit Spielgeld, wertet sich selbst aus."""
import asyncio
import copy
import time
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo

import config as C
import learning as L
from hyperliquid import HLError, Hyperliquid
from portfolio import Portfolio
from selection import candidates
from strategy import consensus, targets

TZ = ZoneInfo(C.TIMEZONE)
MAIN = "main"
CMP = "cmp_"   # feste Vergleichs-Konten (Nicks Wunsch 19.10.2026): gleiche Signale wie das Hauptkonto,
               # nur anderes Risiko/Hebel. Sie lernen nicht mit und werden nie uebernommen oder ersetzt.
CMP_ACCOUNTS = []   # ersetzt durch eigene Copy-Bot-Varianten (Ports 8011-8015), siehe run_copybot.py
CMP_OWN_KEYS = ("leverage", "max_coin_pct", "max_margin_pct", "max_positions", "stop_loss_pct")


def is_cmp(pid):
    return pid.startswith(CMP)
SIZING_KEYS = ("leverage", "max_coin_pct", "max_margin_pct", "max_positions", "rebalance_band", "min_order_eur")


def eur(x):
    s = f"{abs(x):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return ("-" if x < 0 else "") + s + " €"


def pp(x, d=2):
    """Prozent mit Vorzeichen, deutsches Komma."""
    return f"{x:+.{d}f}".replace(".", ",") + " %"


def num(x, d=2):
    return f"{x:.{d}f}".replace(".", ",")


def short(addr):
    return addr[:6] + "…" + addr[-4:]


class Engine:
    def __init__(self, storage):
        self.db = storage
        self.hl = Hyperliquid()
        self.lock = asyncio.Lock()
        self.refreshing = False
        self.market, self.prev_market = {}, {}
        self.states, self.prev_states = {}, {}
        self.state_ts = {}
        self.cons_w, self.cons_u = {}, {}
        self.verdicts = {}
        self.last_cycle = None
        self.cycle_ms = None
        self.conn = {"ok": False, "msg": "startet …"}
        self.last_summary_log = 0
        self.last_snapshot = 0
        self.selftest_result = self.db.get("selftest")
        self.copy_ret_last = {}
        self._load()
        self._ensure_cmp()
        if self.db.get("reset_token") != C.RESET_TOKEN:
            self.reset_keep_rules(f"Neustart mit {eur(C.START_CAPITAL)} auf Nicks Wunsch ({C.RESET_TOKEN})")

    def _ensure_cmp(self):
        """Vergleichs-Konten anlegen, falls sie fehlen (z. B. nach einem Update oder Zuruecksetzen).
        Nicht mehr vorgesehene Vergleichs-Konten werden entfernt."""
        made = False
        wanted = {pid for pid, _, _ in CMP_ACCOUNTS}
        for pid in [p for p in self.pfs if is_cmp(p) and p not in wanted]:
            del self.pfs[pid]
            self.db.delete_portfolio(pid)
            made = True
        for pid, name, own in CMP_ACCOUNTS:
            if pid not in self.pfs:
                params = {**self.main.params, **own}
                pf = Portfolio(pid, name, params, C.START_CAPITAL)
                pf.note = "Vergleich: gleiche Profis und Signale wie das Hauptkonto, nur anderes Risiko/Hebel."
                self.pfs[pid] = pf
                self.db.add_equity(pid, pf.start)
                made = True
        # Neu dazugekommene Schatten-Strategien (z. B. „Wende-Schutz“) einmalig nachrüsten.
        # Fliegt eine später beim Lernen raus, wird sie nicht wieder angelegt.
        added = set(self.db.get("added_variants", []) or [])
        for vid, name, params in L.initial_variants(C.DEFAULT_PARAMS):
            if vid in self.pfs or vid in added:
                added.add(vid)
                continue
            if vid in ("v_turn", "v_calm"):
                pf = Portfolio(vid, name, params, C.START_CAPITAL)
                pf.note = {"v_turn": "Neu: nicht alles auf eine Richtung setzen – weniger Verlust, wenn der Markt dreht.",
                           "v_calm": "Neu: seltener tauschen – ein Coin bleibt mind. 60 min, ein neuer muss 2x stärker sein."}[vid]
                self.pfs[vid] = pf
                self.db.add_equity(vid, pf.start)
                self.log(f"Neue Schatten-Strategie im Test: „{name}“.")
                made = True
            added.add(vid)
        self.db.put("added_variants", sorted(added))
        # 20.10.2026: Hebel überall halbiert (Nick). Bestehende Konten (Haupt + Schatten) einmalig umstellen.
        if not self.db.get("lev_halved"):
            for pf in self.pfs.values():
                pf.params["leverage"] = round(pf.params["leverage"] / 2, 2)
            self.db.put("lev_halved", True)
            self.log(f"Hebel halbiert: jetzt {self.main.params['leverage']:g}. Positionen werden schrittweise angepasst.")
            made = True
        # 21.10.2026: Hebel wieder auf den Standard dieses Bots (Copy-Bot 3, Varianten ihr eigener Wert). Einmalig.
        if not self.db.get("lev_set_v2"):
            self.db.put("lev_set_v2", True)
            target = float(C.DEFAULT_PARAMS["leverage"])
            if self.main.params.get("leverage") != target:
                self.main.params["leverage"] = target
                self.log(f"Hebel jetzt {target:g}. Positionen werden schrittweise angepasst.")
                made = True
        if made:
            self.save()

    # ================================================================ Zustand
    def _load(self):
        s = self.db.get("state")
        if not s:
            self._fresh_state()
            return
        self.settings = {**C.DEFAULT_SETTINGS, **s.get("settings", {})}
        self.status = s.get("status", "running")
        self.traders = s.get("traders", [])
        self.reserves = s.get("reserves", [])
        self.excluded = s.get("excluded", {})
        self.trust = self.db.get("trust", {}) or {}
        self.ts = s.get("ts", {})
        self.selection_info = s.get("selection_info", {})
        self.learned = s.get("learned", [])
        self.pfs = {}
        for d in s.get("portfolios", []):
            d["params"] = {**C.DEFAULT_PARAMS, **d["params"]}
            self.pfs[d["id"]] = Portfolio.from_dict(d)
        if MAIN not in self.pfs:
            self._fresh_state()

    def _fresh_state(self):
        self.settings = dict(C.DEFAULT_SETTINGS)
        self.status = "running"
        self.traders = self.db.get("traders", []) or []
        self.reserves = []
        self.excluded = {}
        self.trust = self.db.get("trust", {}) or {}
        self.ts = {"started": time.time(), "last_lb": 0, "last_eval": time.time(), "last_switch": time.time(),
                   "last_mutation": time.time()}
        self.selection_info = {}
        self.learned = []
        self.pfs = {MAIN: Portfolio(MAIN, "Hauptkonto", C.DEFAULT_PARAMS, C.START_CAPITAL)}
        for vid, name, params in L.initial_variants(C.DEFAULT_PARAMS):
            self.pfs[vid] = Portfolio(vid, name, params, C.START_CAPITAL)
        for pf in self.pfs.values():
            self.db.add_equity(pf.id, pf.start)
        self.db.event("start", "Testkonto eröffnet", f"Startkapital {eur(C.START_CAPITAL)} Spielgeld.")
        self.db.put("lev_halved", True)      # neue Konten starten schon mit dem halbierten Hebel
        self.db.put("lev_set_v2", True)
        self.db.put("reset_token", C.RESET_TOKEN)
        self.save()

    def save(self):
        self.db.put("state", {
            "settings": self.settings, "status": self.status, "traders": self.traders, "reserves": self.reserves,
            "excluded": self.excluded, "ts": self.ts, "selection_info": self.selection_info, "learned": self.learned,
            "portfolios": [pf.to_dict() for pf in self.pfs.values()],
        })
        self.db.put("trust", self.trust)
        self.db.put("traders", self.traders)

    @property
    def main(self):
        return self.pfs[MAIN]

    def log(self, msg, level="info"):
        self.db.log(msg, level)

    # ================================================================ Hauptschleife
    async def run(self):
        self.log("Bot gestartet. Spielgeld-Modus – es wird nichts an einer echten Börse ausgeführt.")
        asyncio.create_task(self._live_prices())
        while True:
            t0 = time.time()
            try:
                async with self.lock:
                    await self.cycle()
                self.conn = {"ok": True, "msg": "Verbunden mit Hyperliquid (nur lesen)"}
            except HLError as e:
                self.conn = {"ok": False, "msg": f"Verbindung gestört: {e}"}
                self.log(f"Daten konnten nicht geladen werden ({e}). Neuer Versuch gleich.", "warn")
            except Exception as e:      # der Bot darf nie ganz stehen bleiben
                self.conn = {"ok": False, "msg": f"Fehler: {e}"}
                self.log(f"Unerwarteter Fehler: {e}", "error")
                traceback.print_exc()
            self.cycle_ms = round((time.time() - t0) * 1000)
            await asyncio.sleep(max(5, self.settings["cycle_seconds"] - (time.time() - t0)))

    async def _live_prices(self):
        """Alle 2 Sekunden frische Kurse fürs Dashboard (Gewinn/Verlust live).
        Gehandelt und gelernt wird weiter nur in der normalen Runde."""
        while True:
            await asyncio.sleep(2)
            if not self.market or self.lock.locked():
                continue
            try:
                mids = await self.hl.info({"type": "allMids"}, retries=1)
                if self.lock.locked() or not isinstance(mids, dict):
                    continue
                new = {}
                for coin, m in self.market.items():
                    px = mids.get(coin)
                    new[coin] = {**m, "mid": float(px), "mark": float(px)} if px else m
                self.market = new
            except Exception:
                pass

    async def cycle(self):
        now = time.time()
        market = await self.hl.market()
        if not self.traders:
            await self.refresh_traders(blocking=True)
        elif now - self.ts.get("last_lb", 0) > C.LEADERBOARD_REFRESH_HOURS * 3600 and not self.refreshing:
            asyncio.create_task(self.refresh_traders())

        states = await self.fetch_states(self.traders)
        fresh = sum(1 for t in self.traders if self.state_ts.get(t["address"], 0) >= now - 1)
        if self.traders and fresh < len(self.traders) * 0.5:
            raise HLError(f"nur {fresh} von {len(self.traders)} Profis abrufbar")

        dt = min(300.0, now - self.last_cycle) if self.last_cycle else 0.0
        if self.prev_states and self.prev_market and dt > 0:
            self.copy_ret_last = L.update_trust(self.trust, self.traders, states, self.prev_states,
                                                market, self.prev_market, dt)
            L.snapshot_trust_history(self.trust)
        self.drop_weak_traders(now)

        self.market = market
        self.states = states
        self.cons_w = consensus(self.traders, states, self.trust, market, use_trust=True)
        self.cons_u = consensus(self.traders, states, self.trust, market, use_trust=False)
        date_str = datetime.now(TZ).strftime("%Y-%m-%d")

        for pf in self.pfs.values():
            if is_cmp(pf.id):
                own = {k: pf.params[k] for k in CMP_OWN_KEYS}
                pf.params.update({k: v for k, v in self.main.params.items() if k not in CMP_OWN_KEYS})
                pf.params.update(own)
            elif pf.id != MAIN:
                for k in SIZING_KEYS:
                    pf.params[k] = self.main.params[k]
            if dt > 0:
                pf.apply_funding(market, dt)
            closed = pf.check_risk(market, now)
            active = pf.id != MAIN or self.status == "running"
            if is_cmp(pf.id):
                active = self.status == "running" and not pf.params.get("cmp_stopped")
            if active:
                cons = self.cons_w if pf.params.get("use_trust", True) else self.cons_u
                eq = pf.equity(market)
                tg, reasons, sup, verdict = targets(pf, cons, eq, now)
                if pf.id == MAIN:
                    self.verdicts = verdict
                before = {c: p["qty"] for c, p in pf.positions.items()}
                closed += pf.rebalance(tg, market, now, sup, reasons)
                if pf.id == MAIN:
                    self.log_main_orders(before, cons, market)
            for t in closed:
                self.db.add_trade(t)
                if pf.id == MAIN:
                    self.log(f"{'Gewinn' if t['net'] >= 0 else 'Verlust'} {eur(t['net'])}: {t['coin']} {t['side']} "
                             f"geschlossen ({t['reason']}).", "trade")
            eq = pf.equity(market)
            pf.peak = max(pf.peak, eq)
            pf.roll_day(date_str, market)
            if pf.id == MAIN:
                self.drawdown_guard(eq, now)
            elif is_cmp(pf.id) and not pf.params.get("cmp_stopped") \
                    and eq < pf.peak * (1 - self.settings["max_drawdown_pct"]):
                for t in pf.close_all(market, "Not-Aus (Verlust-Grenze)", now):
                    self.db.add_trade(t)
                pf.params["cmp_stopped"] = True
                self.log(f"{pf.name}: mehr als {num(self.settings['max_drawdown_pct'] * 100, 0)} % unter dem "
                         f"Höchststand – alle Positionen geschlossen, dieses Vergleichs-Konto handelt nicht mehr.", "warn")

        if now - self.last_snapshot >= C.EQUITY_SNAPSHOT_SECONDS:
            for pf in self.pfs.values():
                self.db.add_equity(pf.id, pf.equity(market), now)
            self.last_snapshot = now
        if now - self.ts.get("last_eval", 0) >= C.EVALUATION_HOURS * 3600:
            self.evaluate()
        if now - self.last_summary_log >= 900:
            busy = sum(1 for t in self.traders if self.states.get(t["address"], {}).get("positions"))
            self.log(f"Prüft {len(self.traders)} Profis: {busy} haben gerade offene Positionen in "
                     f"{len(self.cons_w)} Coins. Bot hält {len(self.main.positions)} Positionen.")
            self.last_summary_log = now

        self.prev_states, self.prev_market = states, market
        self.last_cycle = now
        self.save()

    def log_main_orders(self, before, cons, market):
        for coin, p in self.main.positions.items():
            old = before.get(coin, 0.0)
            if old == p["qty"]:
                continue
            c = cons.get(coin, {})
            share = c.get("long_share", 0.5) if p["qty"] > 0 else 1 - c.get("long_share", 0.5)
            ntl = abs(p["qty"]) * market[coin]["mid"]
            if old == 0 or (old > 0) != (p["qty"] > 0):
                self.log(f"Eröffnet {coin} {'long' if p['qty'] > 0 else 'short'} mit {eur(ntl)} – "
                         f"{c.get('n', 0)} Profis drin, {num(share * 100, 0)} % auf dieser Seite.", "trade")
            else:
                self.log(f"Passt {coin} an: jetzt {eur(ntl)} (Profis {'stocken auf' if abs(p['qty']) > abs(old) else 'bauen ab'}).", "trade")

    async def fetch_states(self, traders):
        async def one(t):
            try:
                st = await self.hl.positions(t["address"])
                self.state_ts[t["address"]] = time.time()
                return t["address"], st
            except HLError:
                return t["address"], None
        res = await asyncio.gather(*(one(t) for t in traders))
        out = {}
        for a, st in res:
            if st is not None:
                out[a] = st
            elif a in self.states:
                out[a] = self.states[a]    # alter Stand, besser als nichts
        return out

    # ================================================================ Profis auswählen
    async def refresh_traders(self, blocking=False):
        if self.refreshing:
            return
        self.refreshing = True
        try:
            self.log("Lädt die öffentliche Bestenliste von Hyperliquid und sucht die Top-Profis …")
            rows = await self.hl.leaderboard()
            pool = await asyncio.to_thread(candidates, rows)
            total_rows = len(rows)
            del rows
            now = time.time()
            self.excluded = {a: u for a, u in self.excluded.items() if u > now}
            pool = [p for p in pool if p["address"] not in self.excluded]
            # Bewährte Profis behalten einen Bonus, damit Gelerntes nicht bei jeder Auswahl verloren geht
            for p in pool:
                tr = self.trust.get(p["address"])
                p["rank_score"] = p["score"] + (0.08 * (tr["trust"] - 1) if tr else 0)
            pool.sort(key=lambda p: p["rank_score"], reverse=True)
            valid, checked, rejected = [], 0, {"klein": 0, "korb": 0, "fehler": 0}
            for i in range(0, len(pool), 20):
                batch = pool[i:i + 20]
                res = await asyncio.gather(*(self.hl.positions(p["address"]) for p in batch), return_exceptions=True)
                for p, st in zip(batch, res):
                    checked += 1
                    if isinstance(st, Exception):
                        rejected["fehler"] += 1
                        continue
                    if st["account"] < C.SELECTION["min_account_usd"]:
                        rejected["klein"] += 1
                        continue
                    if len(st["positions"]) > C.SELECTION["max_open_positions"]:
                        rejected["korb"] += 1
                        continue
                    p["account"] = st["account"]
                    valid.append(p)
                    self.states[p["address"]] = st
                if len(valid) >= C.TOP_N + 30:
                    break
            if len(valid) < 10:
                self.log("Zu wenige passende Profis gefunden – alte Auswahl bleibt.", "warn")
                return
            old = {t["address"] for t in self.traders}
            prev = {t["address"]: t for t in self.traders}
            chosen = valid[:C.TOP_N]
            for rank, t in enumerate(chosen, 1):
                t["rank"] = rank
                t["base_weight"] = 1.0
                t["added_at"] = prev.get(t["address"], {}).get("added_at", now)
                self.trust.setdefault(t["address"], {"trust": 1.0, "obs": 0, "copy_ret": 0.0, "since": now, "hist": []})
            self.traders = chosen
            self.reserves = valid[C.TOP_N:]
            new = {t["address"] for t in chosen}
            added, removed = len(new - old), len(old - new)
            self.selection_info = {"at": now, "rows": total_rows, "candidates": len(pool), "checked": checked,
                                   "rejected": rejected, "chosen": len(chosen), "reserves": len(self.reserves)}
            self.ts["last_lb"] = now
            msg = (f"{total_rows:,} Konten durchsucht, {len(pool)} passten zu den Regeln, {checked} live geprüft. "
                   f"{len(chosen)} Profis werden kopiert.").replace(",", ".")
            if old:
                msg += f" {added} neu dabei, {removed} raus."
            self.log("Profi-Auswahl aktualisiert: " + msg)
            self.db.event("select", "Profi-Auswahl aktualisiert", msg)
            self.save()
        except Exception as e:
            self.log(f"Bestenliste konnte nicht geladen werden: {e}", "warn")
            if blocking and not self.traders:
                raise HLError(f"Bestenliste: {e}")
        finally:
            self.refreshing = False

    def drop_weak_traders(self, now):
        keep = []
        for t in self.traders:
            tr = self.trust.get(t["address"], {})
            if tr.get("trust", 1) < C.TRUST_DROP_BELOW and now - tr.get("since", now) > 86400 and self.reserves:
                self.excluded[t["address"]] = now + 7 * 86400
                repl = self.reserves.pop(0)
                repl["rank"], repl["base_weight"], repl["added_at"] = t["rank"], 1.0, now
                self.trust.setdefault(repl["address"], {"trust": 1.0, "obs": 0, "copy_ret": 0.0, "since": now, "hist": []})
                keep.append(repl)
                msg = (f"{t.get('name') or short(t['address'])} aussortiert (Vertrauen {num(tr['trust'])}×, "
                       f"Kopieren hätte {pp(tr.get('copy_ret', 0) * 100, 1)} gebracht). Nachrücker: "
                       f"{repl.get('name') or short(repl['address'])}.")
                self.log(msg)
                self.db.event("trust", "Profi aussortiert", msg)
            else:
                keep.append(t)
        self.traders = keep

    # ================================================================ Risiko
    def drawdown_guard(self, eq, now):
        if self.status != "running":
            return
        if eq < self.main.peak * (1 - self.settings["max_drawdown_pct"]):
            closed = self.main.close_all(self.market, "Not-Aus (Verlust-Grenze)", now)
            for t in closed:
                self.db.add_trade(t)
            self.status = "stopped"
            msg = (f"Topf ist mehr als {num(self.settings['max_drawdown_pct'] * 100, 0)} % unter den Höchststand gefallen. "
                   f"Alle Positionen geschlossen, Bot gestoppt.")
            self.log(msg, "error")
            self.db.event("risk", "Automatischer Not-Aus", msg)

    # ================================================================ Selbstauswertung
    def evaluate(self, manual=False):
        now = time.time()
        self.ts["last_eval"] = now
        notes = []
        window_start = max(now - 7 * 86400, self.ts.get("last_switch", 0))
        stats = {}
        for pf in self.pfs.values():
            since = max(window_start, pf.created_at)
            stats[pf.id] = L.window_stats(self.db, pf.id, since, pf.equity(self.market))
            stats[pf.id]["age_h"] = (now - pf.created_at) / 3600
        self.db.put("eval_stats", stats)
        main_s = stats[MAIN]
        variants = [pf for pf in self.pfs.values() if pf.id != MAIN and not is_cmp(pf.id)]
        ranked = sorted(variants, key=lambda pf: stats[pf.id]["score"], reverse=True)
        best = ranked[0] if ranked else None

        # 1) Strategie wechseln?
        if best:
            bs = stats[best.id]
            same = all(best.params.get(k) == self.main.params.get(k) for k in L.LEARNABLE) and \
                best.params.get("use_trust") == self.main.params.get("use_trust")
            ready = bs["trades"] >= C.SWITCH_MIN_TRADES and bs["age_h"] >= C.SWITCH_MIN_HOURS
            better = bs["score"] - main_s["score"] >= C.SWITCH_MARGIN_PCT
            if same:
                notes.append(f"Beste Schatten-Strategie ist „{best.name}“ – der Bot nutzt deren Regeln schon.")
            elif ready and better and self.settings["auto_learn"]:
                old = copy.deepcopy(self.main.params)
                for k in list(L.LEARNABLE) + ["use_trust"]:
                    self.main.params[k] = best.params[k]
                self.ts["last_switch"] = now
                msg = (f"Übernimmt die Regeln von „{best.name}“ ({pp(bs['ret_pct'])} gegenüber "
                       f"{pp(main_s['ret_pct'])}, {bs['trades']} Trades). Geändert: "
                       f"{L.describe_params(self.main.params, old)}.")
                notes.append(msg)
                self.log("Gelernt: " + msg)
                self.db.event("switch", "Strategie verbessert", msg)
            elif ready and better:
                notes.append(f"„{best.name}“ wäre besser, aber Auto-Lernen ist aus. Du kannst die Regeln unten übernehmen.")
            elif not ready:
                notes.append(f"„{best.name}“ führt gerade ({pp(bs['ret_pct'])}), hat aber erst {bs['trades']} Trades "
                             f"in {num(bs['age_h'], 0)} h – der Bot wartet auf mind. {C.SWITCH_MIN_TRADES} Trades und "
                             f"{C.SWITCH_MIN_HOURS} h, damit es kein Zufall ist.")
            else:
                notes.append(f"Hauptkonto liegt gleichauf oder vorne ({pp(main_s['ret_pct'])}) – keine Änderung nötig.")

        # 2) Schwächste Variante ersetzen (Evolution)
        if now - self.ts.get("last_mutation", 0) >= C.MUTATE_EVERY_HOURS * 3600 and len(ranked) >= 3:
            pool = [pf for pf in ranked if pf.id != "v_control" and stats[pf.id]["age_h"] >= C.SWITCH_MIN_HOURS]
            if len(pool) >= 2:
                worst = pool[-1]
                parent = ranked[0] if ranked[0].id != "v_control" else pool[0]
                params, keys = L.mutate(parent.params)
                n = self.db.get("mutation_count", 0) + 1
                self.db.put("mutation_count", n)
                new_id = f"v_m{n}"
                name = f"Abwandlung #{n}"
                self.db.delete_portfolio(worst.id)
                del self.pfs[worst.id]
                pf = Portfolio(new_id, name, params, C.START_CAPITAL)
                pf.note = f"Abgeleitet von „{parent.name}“: {L.describe_params(params, parent.params)}"
                self.pfs[new_id] = pf
                self.db.add_equity(new_id, pf.start)
                self.ts["last_mutation"] = now
                msg = (f"„{worst.name}“ ({pp(stats[worst.id]['ret_pct'])}) ersetzt durch „{name}“ – "
                       f"{pf.note}.")
                notes.append(msg)
                self.log("Neue Strategie im Test: " + msg)
                self.db.event("mutate", "Neue Strategie im Test", msg)

        # 3) Coins sperren, die dem Bot nicht liegen
        for c in L.coins_to_block(self.db, MAIN, now):
            if self.main.blocked.get(c["coin"], 0) < now:
                self.main.blocked[c["coin"]] = now + C.COIN_BLOCK_DAYS * 86400
                msg = (f"{c['coin']} für {C.COIN_BLOCK_DAYS} Tage gesperrt: {c['n']} Trades, "
                       f"{c['wins']} mit Gewinn, zusammen {eur(c['net'])}.")
                notes.append(msg)
                self.db.event("block", "Coin gesperrt", msg)
                self.log("Gelernt: " + msg)

        # 4) Vertrauen zusammenfassen
        if self.trust and self.traders:
            ts = sorted(((self.trust.get(t["address"], {}).get("trust", 1), t) for t in self.traders),
                        key=lambda x: x[0], reverse=True)
            hi = [t for v, t in ts if v > 1.3]
            lo = [t for v, t in ts if v < 0.7]
            notes.append(f"Vertrauen: {len(hi)} Profis zählen jetzt deutlich mehr (über 1,3×), {len(lo)} deutlich "
                         f"weniger (unter 0,7×). Am meisten vertraut der Bot "
                         f"{ts[0][1].get('name') or short(ts[0][1]['address'])} ({num(ts[0][0])}×).")
        self.learned = [{"ts": now, "text": n} for n in notes]
        if manual:
            self.log("Auswertung auf Knopfdruck durchgeführt.")
        self.save()
        return notes

    # ================================================================ Bedienung
    def control(self, action):
        now = time.time()
        if action == "pause":
            self.status = "paused"
            self.log("Pausiert: keine neuen Trades. Stop-Loss bleibt aktiv, Schattenkonten lernen weiter.")
        elif action == "resume":
            self.status = "running"
            self.main.peak = max(self.main.equity(self.market), 1)
            self.log("Läuft wieder.")
        elif action == "kill":
            closed = self.main.close_all(self.market, "Not-Aus (von dir)", now)
            for t in closed:
                self.db.add_trade(t)
            self.status = "stopped"
            self.log(f"Not-Aus: {len(closed)} Positionen geschlossen, Bot gestoppt.", "error")
            self.db.event("risk", "Not-Aus gedrückt", f"{len(closed)} Positionen geschlossen.")
        self.save()

    def update_params(self, params, settings):
        changed = []
        for k, v in (params or {}).items():
            if k in C.DEFAULT_PARAMS and v is not None:
                v = type(C.DEFAULT_PARAMS[k])(v)
                if self.main.params.get(k) != v:
                    changed.append(k)
                    self.main.params[k] = v
        for k, v in (settings or {}).items():
            if k in C.DEFAULT_SETTINGS and v is not None:
                v = type(C.DEFAULT_SETTINGS[k])(v)
                if self.settings.get(k) != v:
                    changed.append(k)
                    self.settings[k] = v
        p = self.main.params
        p["leverage"] = min(10.0, max(0.5, p["leverage"]))
        p["max_coin_pct"] = min(1.0, max(0.02, p["max_coin_pct"]))
        p["max_margin_pct"] = min(0.95, max(0.05, p["max_margin_pct"]))
        p["stop_loss_pct"] = min(0.5, max(0.005, p["stop_loss_pct"]))
        p["min_traders"] = max(1, p["min_traders"])
        p["max_positions"] = max(1, min(50, p["max_positions"]))
        self.settings["cycle_seconds"] = max(20, min(600, self.settings["cycle_seconds"]))
        if changed:
            self.log(f"Einstellungen von dir geändert: {', '.join(changed)}.")
            self.save()
        return changed

    def adopt_variant(self, vid):
        pf = self.pfs.get(vid)
        if not pf or vid == MAIN:
            return False
        old = copy.deepcopy(self.main.params)
        for k in list(L.LEARNABLE) + ["use_trust"]:
            self.main.params[k] = pf.params[k]
        self.ts["last_switch"] = time.time()
        msg = f"Regeln von „{pf.name}“ von dir übernommen: {L.describe_params(self.main.params, old)}."
        self.log(msg)
        self.db.event("switch", "Strategie von dir übernommen", msg)
        self.save()
        return True

    def reset(self, keep_learning=True):
        self.db.wipe(keep_learning=keep_learning)
        if not keep_learning:
            self.trust = {}
        self.copy_ret_last = {}
        self._fresh_state()
        self._ensure_cmp()
        if keep_learning:
            self.trust = self.db.get("trust", {}) or {}
        self.log("Testkonto zurückgesetzt" + (" – Gelerntes über die Profis bleibt erhalten." if keep_learning else " – komplett neu."))

    def reset_keep_rules(self, why):
        """Konten auf Startkapital zurücksetzen, aber ALLES Gelernte behalten: Vertrauen und Auswahl der Profis,
        gelernte Regeln des Hauptkontos, Schatten-Strategien (inkl. Abwandlungen) und gesperrte Coins."""
        main_params = copy.deepcopy(self.main.params)
        blocked = dict(self.main.blocked)
        variants = [(pf.id, pf.name, copy.deepcopy(pf.params), pf.note) for pf in self.pfs.values()
                    if pf.id != MAIN and not is_cmp(pf.id)]
        learned = list(self.learned)
        keep_kv = {k: self.db.get(k) for k in ("mutation_count", "added_variants") if self.db.get(k) is not None}
        self.reset(keep_learning=True)
        self.main.params.update(main_params)
        self.main.blocked = blocked
        for pid in [p for p in self.pfs if p != MAIN and not is_cmp(p)]:
            del self.pfs[pid]
        for vid, name, params, note in variants:
            pf = Portfolio(vid, name, params, C.START_CAPITAL)
            pf.note = note
            self.pfs[vid] = pf
            self.db.add_equity(vid, pf.start)
        self.learned = learned
        for k, v in keep_kv.items():
            self.db.put(k, v)
        self.db.put("lev_halved", True)
        self.db.put("lev_set_v2", True)
        self.db.put("reset_token", C.RESET_TOKEN)
        self.save()
        self.log(f"{why}. Gelerntes bleibt: Profi-Vertrauen, Regeln, Schatten-Strategien, gesperrte Coins.")

    async def selftest(self):
        checks = []
        t0 = time.time()
        try:
            m = await self.hl.market()
            checks.append(("Hyperliquid erreichbar", True, f"{len(m)} Coins, Antwort in {round((time.time() - t0) * 1000)} ms"))
        except Exception as e:
            checks.append(("Hyperliquid erreichbar", False, str(e)))
            m = self.market
        now = time.time()
        fresh = sum(1 for t in self.traders if now - self.state_ts.get(t["address"], 0) < self.settings["cycle_seconds"] * 3)
        checks.append(("Positionen der Profis aktuell", fresh >= len(self.traders) * 0.8 and self.traders,
                       f"{fresh} von {len(self.traders)} in den letzten Runden geladen"))
        for pf in [self.main]:
            u = pf.unrealized(self.market)
            expect = pf.start + pf.totals["realized"] - pf.totals["fees"] + pf.totals["funding"] + u
            diff = abs(expect - pf.equity(self.market))
            checks.append(("Buchhaltung stimmt", diff < 0.01,
                           f"Start + Gewinne − Gebühren + Funding + offen = {eur(expect)} (Abweichung {diff:.4f} €)"))
            eq = pf.equity(self.market)
            margin = pf.gross(self.market) / pf.params["leverage"]
            checks.append(("Margin im Rahmen", margin <= eq * 1.01 or not pf.positions,
                           f"gebunden {eur(margin)} von {eur(eq)}"))
        stale = [c for c in self.main.positions if c not in m]
        checks.append(("Alle Positionen haben Kurse", not stale, ", ".join(stale) or "ja"))
        try:
            self.db.put("selftest_probe", now)
            checks.append(("Datenbank schreibbar", True, str(C.DB_PATH.name)))
        except Exception as e:
            checks.append(("Datenbank schreibbar", False, str(e)))
        ok = all(c[1] for c in checks)
        self.selftest_result = {"ts": now, "ok": bool(ok),
                                "checks": [{"name": n, "ok": bool(o), "detail": d} for n, o, d in checks]}
        self.db.put("selftest", self.selftest_result)
        self.log(f"Selbsttest: {'alles in Ordnung' if ok else 'es gibt Auffälligkeiten'}.", "info" if ok else "warn")
        return self.selftest_result
