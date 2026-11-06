"""Verbindung zur Boerse (paper / testnet / live).

Unterstuetzt:
  exchange: binance  -> Binance Spot oder USDT-Futures (Standard-Quote USDT)
  exchange: bybiteu  -> Bybit EU (MiCA-lizenziert, Oesterreich). NUR Spot, Quote USDC oder EUR.
  exchange: krakenfutures -> Kraken Futures (EU: MiCA + MiFID II). NUR Futures (Perpetuals, Quote USD),
                     beide Richtungen, bis 10x. Testmodus = Kraken-Demo (demo-futures.kraken.com).
"""
import os
import time
from datetime import datetime

import ccxt

STABLES = {"USDT", "USDC", "FDUSD", "TUSD", "BUSD", "DAI", "USDP", "EUR", "USDE", "PYUSD", "AEUR", "XUSD",
           "USDQ", "EURQ", "EURC", "EURI", "RLUSD", "PLN"}

EXCHANGES = {
    # name: (Spot-Klasse, Futures-Klasse oder None, Key-Variable, Secret-Variable, Anzeigename, Standard-Quote)
    "binance": (ccxt.binance, ccxt.binanceusdm, "BINANCE_API_KEY", "BINANCE_API_SECRET", "Binance", "USDT"),
    "bybiteu": (ccxt.bybiteu, None, "BYBIT_API_KEY", "BYBIT_API_SECRET", "Bybit EU", "USDC"),
    "krakenfutures": (None, ccxt.krakenfutures, "KRAKEN_FUTURES_API_KEY", "KRAKEN_FUTURES_API_SECRET", "Kraken Futures", "USD"),
}


def kraken_accounts(ex):
    """Kraken Futures: alle Unterkonten roh abfragen. Gibt (Wert in USD, Name des Kontos, Liste aller Konten).
    Fuer Perpetuals (PF_...) zaehlt das Multi-Collateral-Konto ("flex"). Fehlt es, Bargeld-Konto (cash) in USD."""
    resp = ex.privateGetAccounts() or {}
    if isinstance(resp, str):
        import json
        resp = json.loads(resp)
    accs = resp.get("accounts") if isinstance(resp, dict) else None
    if isinstance(accs, list):      # manche Antworten liefern eine Liste statt eines Objekts
        accs = {str(a.get("name") or a.get("type") or i): a for i, a in enumerate(accs) if isinstance(a, dict)}
    accs = {k: v for k, v in (accs or {}).items() if isinstance(v, dict)}
    overview = [(name, a.get("type")) for name, a in accs.items()]
    for name, a in accs.items():
        if a.get("type") == "multiCollateralMarginAccount" or name == "flex":
            return float(a.get("balanceValue") or a.get("portfolioValue") or 0), name, overview
    bal = (accs.get("cash") or {}).get("balances")
    cash = bal if isinstance(bal, dict) else {}
    return float(cash.get("usd") or 0), ("cash" if "cash" in accs else None), overview


def exchange_info(cfg):
    name = cfg.get("exchange", "binance")
    if name not in EXCHANGES:
        raise SystemExit(f"Unbekannte Boerse '{name}' in config.yaml. Moeglich: {', '.join(EXCHANGES)}")
    return EXCHANGES[name]


class Exchange:
    def __init__(self, cfg):
        self.cfg = cfg
        self.mode = cfg["mode"]
        self.name = cfg.get("exchange", "binance")
        spot_cls, fut_cls, key_var, sec_var, self.label, default_quote = exchange_info(cfg)
        self.quote = cfg.get("quote") or default_quote
        if fut_cls is None and cfg["market"] != "spot":
            print(f"Hinweis: {self.label} bietet keine Futures. Der Bot handelt dort Spot (nur Long, ohne Hebel).")
            cfg["market"] = "spot"
        if spot_cls is None and cfg["market"] == "spot":
            print(f"Hinweis: {self.label} ist reine Futures-Boerse. Der Bot handelt dort Futures.")
            cfg["market"] = "futures"
        self.is_spot = cfg["market"] == "spot"
        self.cls = spot_cls if self.is_spot else fut_cls
        opts = {"enableRateLimit": True, "options": {"defaultType": "spot" if self.is_spot else "swap"}}

        # Kurse und Kerzen immer vom echten Markt (Testnet-Kurse sind unbrauchbar)
        self.public = self.cls(dict(opts))
        self.public.load_markets()

        self.ex = None
        if self.mode in ("testnet", "live"):
            key, sec = os.getenv(key_var, ""), os.getenv(sec_var, "")
            if not key or not sec:
                raise SystemExit(f"{key_var} / {sec_var} fehlen in der Datei .env")
            # PC-Uhr kann ein paar Sekunden falsch gehen -> Zeitabstand zur Boerse automatisch ausgleichen
            # (sonst Fehler -1021 "Timestamp ... outside of the recvWindow")
            priv = {**opts, "options": {**opts["options"], "adjustForTimeDifference": True, "recvWindow": 10000}}
            self.ex = self.cls({**priv, "apiKey": key, "secret": sec})
            if self.mode == "testnet" and self.name == "krakenfutures":
                raise SystemExit("Kraken hat sein Futures-Demo abgeschaltet. Fuer Tests 'mode: paper' nehmen "
                                 "(Kraken-Kurse, simulierte Orders) oder Echtgeld mit 'mode: live'.")
            if self.mode == "testnet":
                if self.name == "binance":
                    # Binance hat das alte Testnet durch "Demo Trading" (demo.binance.com) ersetzt
                    if not hasattr(self.ex, "enable_demo_trading"):
                        raise SystemExit("ccxt ist zu alt fuer Binance Demo Trading. Bitte: pip install -U ccxt")
                    self.ex.enable_demo_trading(True)
                else:
                    self.ex.set_sandbox_mode(True)
            self.ex.load_markets()

        self.paper_balance = float(cfg["paper_start_balance"])
        self._ratio_cache, self._usdm = {}, None
        self.blocked = set()   # Coins, die die Boerse fuer dieses Konto ablehnt (z. B. Aktien-Perps)

    # ---- Symbol-Namen ------------------------------------------------------
    def resolve(self, symbol):
        """BTC/USDT -> boersen-interner Name (Futures: BTC/USDT:USDT). None wenn unbekannt."""
        for cand in (symbol, symbol + ":" + self.quote):
            if cand in self.public.markets:
                return cand
        try:
            return self.public.market(symbol)["symbol"]
        except Exception:
            return None

    def is_valid(self, symbol) -> bool:
        return self.resolve(symbol) is not None

    def _trade_sym(self, symbol):
        """Name fuer das Konto (Testnet hat eigene Maerkte, gleiche Namen)."""
        return self.resolve(symbol) or symbol

    # ---- Marktdaten -------------------------------------------------------
    def ohlcv(self, symbol, timeframe, limit=200):
        return self.public.fetch_ohlcv(self._trade_sym(symbol), timeframe, limit=limit)

    def history(self, symbol, timeframe, days):
        """Historische Kerzen fuer den Backtest (eigene Verbindung, blockiert den Bot nicht)."""
        inst = self.cls({"enableRateLimit": True, "options": dict(self.public.options)})
        inst.set_markets(self.public.markets, self.public.currencies)
        sym = self._trade_sym(symbol)
        ms = inst.parse_timeframe(timeframe) * 1000
        since = inst.milliseconds() - days * 86_400_000
        out = []
        while since < inst.milliseconds() - ms:
            batch = inst.fetch_ohlcv(sym, timeframe, since=since, limit=1000)
            if not batch:
                break
            out += batch
            since = batch[-1][0] + ms
            if len(batch) < 2:
                break
        seen, clean = set(), []
        for c in out:
            if c[0] not in seen:
                seen.add(c[0]); clean.append(c)
        return clean

    def price(self, symbol) -> float:
        return float(self.public.fetch_ticker(self._trade_sym(symbol))["last"])

    def prices(self, symbols) -> dict:
        """Alle Kurse in einem Aufruf (schont das Rate-Limit)."""
        mapping = {self._trade_sym(s): s for s in symbols}
        # Binance: "letzte Preise" kosten Gewicht 2 statt 40 (24h-Ticker aller Coins).
        # Wichtig, wenn mehrere Bots von einem PC aus laufen (gemeinsames IP-Limit).
        if str(getattr(self.public, "id", "")).startswith("binance") and self.public.has.get("fetchLastPrices"):
            try:
                lp = self.public.fetch_last_prices(list(mapping))
                out = {}
                for real, mine in mapping.items():
                    p = (lp.get(real) or lp.get(mine) or {}).get("price")
                    if p:
                        out[mine] = float(p)
                if out:
                    return out
            except Exception:
                pass   # Fallback unten
        t = self.public.fetch_tickers(list(mapping))
        out = {}
        for real, mine in mapping.items():
            tick = t.get(real) or t.get(mine)
            if tick and tick.get("last"):
                out[mine] = float(tick["last"])
        return out

    def universe(self, min_quote_volume):
        """Alle handelbaren Coins (gegen die Quote-Waehrung) mit genug Umsatz, fuer den Scanner."""
        tickers = self.public.fetch_tickers()
        rows = []
        for real, t in tickers.items():
            m = self.public.markets.get(real)
            if not m or not m.get("active", True) or m.get("quote") != self.quote:
                continue
            if self.is_spot and not m.get("spot"):
                continue
            if not self.is_spot and not (m.get("swap") and m.get("linear")):
                continue
            if not self.is_crypto_market(m) or real.split(":")[0] in self.blocked:
                continue
            base = m["base"]
            if base in STABLES or base.endswith(("UP", "DOWN", "BULL", "BEAR")):
                continue
            vol = t.get("quoteVolume") or 0
            if vol < min_quote_volume or not t.get("last"):
                continue
            rows.append({"symbol": f"{base}/{self.quote}", "price": float(t["last"]),
                         "change24": float(t.get("percentage") or 0), "volume": float(vol)})
        return rows

    @staticmethod
    def is_crypto_market(m):
        """Binance-Futures listet auch Aktien/ETF/Rohstoff-Perps ("TradFi-Perps", z. B. MSTR, SOXL, KORU).
        Die brauchen einen extra Vertrag, handeln nach Boersenzeiten und passen nicht zur Strategie."""
        info = m.get("info") or {}
        ut = info.get("underlyingType")
        if ut and str(ut).upper() != "COIN":
            return False
        ct = info.get("contractType")
        if ct and str(ct).upper() not in ("PERPETUAL", ""):
            return False
        return True

    # ---- Vor dem Echtgeld-Start: darf dieses Konto ueberhaupt handeln? -------
    def preflight(self):
        """Prueft API-Key-Rechte und Handelserlaubnis. Gibt {"ok", "problems", "notes"} zurueck.
        Faengt u. a. ab: EU-Einschraenkungen (MiCA), fehlende Futures-Freigabe, Auszahlungsrecht am Key."""
        problems, notes = [], []
        if self.mode != "live":
            return {"ok": True, "problems": [], "notes": []}
        if self.name == "bybiteu":
            return self._preflight_bybit()
        if self.name == "krakenfutures":
            return self._preflight_kraken()
        try:
            r = self.ex.sapiGetAccountApiRestrictions()
            if r.get("enableWithdrawals"):
                problems.append("Der API-Key darf AUSZAHLEN. Aus Sicherheitsgruenden startet der Bot so nicht. "
                                "Bei Binance unter API-Verwaltung 'Auszahlungen' deaktivieren.")
            if not r.get("ipRestrict"):
                notes.append("Der API-Key hat keine IP-Beschraenkung. Empfohlen: auf die IP deines PCs begrenzen.")
            if self.is_spot and not r.get("enableSpotAndMarginTrading"):
                problems.append("Der API-Key hat kein Recht fuer Spot-Handel.")
            if not self.is_spot and not r.get("enableFutures"):
                problems.append("Der API-Key hat kein Futures-Recht. In Deutschland oft nicht freischaltbar: "
                                "dann in config.yaml 'market: spot' setzen.")
        except Exception as e:
            notes.append(f"Key-Rechte konnten nicht gelesen werden ({e}).")
        try:
            acc = self.ex.privateGetAccount() if self.is_spot else self.ex.fapiPrivateV2GetAccount()
            if str(acc.get("canTrade")).lower() not in ("true", "1"):
                problems.append("Binance erlaubt diesem Konto gerade keinen Handel"
                                + (" (Spot)." if self.is_spot else " (Futures).")
                                + " Moegliche Gruende: EU-Einschraenkungen seit Juli 2026, Konto-Verifizierung.")
        except Exception as e:
            problems.append(f"Kontoabfrage abgelehnt: {e}. Haeufig: Region/Produkt fuer dein Konto gesperrt.")
        return {"ok": not problems, "problems": problems, "notes": notes}

    def _preflight_kraken(self):
        problems, notes = [], ["Kraken: Den API-Key OHNE Auszahlungsrecht anlegen (nur 'Lesen' und 'Handeln'). "
                               "Der Bot kann das bei Kraken nicht selbst pruefen."]
        try:
            if self.equity() <= 0:
                notes.append("Kein Guthaben im Kraken-Futures-Konto (Flex) gefunden. Geld vom Spot- ins Futures-Konto uebertragen.")
        except Exception as e:
            problems.append(f"Kontoabfrage bei Kraken abgelehnt: {e}. Haeufig: Futures fuer dein Konto noch nicht freigeschaltet "
                            "(Eignungstest) oder falscher Key.")
        return {"ok": not problems, "problems": problems, "notes": notes}

    def _preflight_bybit(self):
        problems, notes = [], []
        try:
            info = self.ex.privateGetV5UserQueryApi().get("result", {})
            perms = info.get("permissions") or {}
            if str(info.get("readOnly")) == "1":
                problems.append("Der Bybit-API-Key ist nur lesend. Fuer den Bot 'Lesen und Schreiben' + Spot-Handel waehlen.")
            if "SpotTrade" not in (perms.get("Spot") or []):
                problems.append("Der Bybit-API-Key hat kein Spot-Handelsrecht.")
            if "Withdraw" in (perms.get("Wallet") or []):
                problems.append("Der API-Key darf AUSZAHLEN. Aus Sicherheitsgruenden startet der Bot so nicht. "
                                "Bei Bybit EU unter API-Verwaltung die Auszahlung deaktivieren.")
            if not info.get("ips") or info.get("ips") == ["*"]:
                notes.append("Der API-Key hat keine IP-Beschraenkung. Empfohlen: auf die IP deines PCs begrenzen.")
        except Exception as e:
            notes.append(f"Key-Rechte konnten nicht gelesen werden ({e}).")
        try:
            bal = self.ex.fetch_balance()
            if float((bal.get("total") or {}).get(self.quote) or 0) <= 0:
                notes.append(f"Kein {self.quote}-Guthaben gefunden. Der Bot handelt nur gegen {self.quote}.")
        except Exception as e:
            problems.append(f"Kontoabfrage abgelehnt: {e}")
        return {"ok": not problems, "problems": problems, "notes": notes}

    # ---- Was machen die Top-Trader? -----------------------------------------
    def top_trader_ratio(self, symbol):
        """Offizielle Binance-Futures-Statistik (oeffentlich, kein Key noetig):
          top_long   = Anteil Long-Positionen bei den Top-Tradern (Top 20 % nach Kontogroesse)
          crowd_long = Anteil Konten, die Long sind, ueber ALLE Trader
        5-Minuten-Raster, 5 Minuten zwischengespeichert. None, wenn es fuer den Coin keine Daten gibt."""
        now = time.time()
        hit = self._ratio_cache.get(symbol)
        if hit and now - hit[0] < 300:
            return hit[1]
        val = None
        try:
            if self._usdm is None:
                use_own = self.name == "binance" and not self.is_spot
                self._usdm = self.public if use_own else ccxt.binanceusdm({"enableRateLimit": True})
            # Statistik kommt immer von Binance-Futures (groesster Markt), Paar BASE+USDT
            sid = symbol.split(":")[0].split("/")[0] + "USDT"
            q = {"symbol": sid, "period": "5m", "limit": 1}
            top = self._usdm.fapiDataGetTopLongShortPositionRatio(q)
            crowd = self._usdm.fapiDataGetGlobalLongShortAccountRatio(q)
            if top and crowd:
                val = {"top_long": round(float(top[-1]["longAccount"]), 4),
                       "crowd_long": round(float(crowd[-1]["longAccount"]), 4)}
        except Exception:
            val = None
        self._ratio_cache[symbol] = (now, val)
        return val

    # ---- Uhrzeit ----------------------------------------------------------
    def resync_time(self, force=False):
        """PC-Uhr und Boersen-Uhr neu abgleichen (Fehler -1021: PC-Uhr geht vor/nach).
        ccxt misst den Abstand sonst nur einmal beim Start; die PC-Uhr driftet aber mit der Zeit."""
        import time as _t
        if not self.ex or not hasattr(self.ex, "load_time_difference"):
            return False
        if not force and _t.time() - getattr(self, "_time_sync", 0) < 1800:
            return False
        self._time_sync = _t.time()
        try:
            self.ex.load_time_difference()
            return True
        except Exception:  # noqa: BLE001
            return False

    # ---- Konto ------------------------------------------------------------
    def equity(self) -> float:
        if self.mode == "paper":
            return self.paper_balance
        self.resync_time()          # alle 30 Minuten Uhr neu abgleichen
        if self.name == "krakenfutures":
            # Kraken "Flex"-Konto: mehrere Waehrungen als Sicherheit -> Gesamtwert in USD
            return kraken_accounts(self.ex)[0]
        bal = self.ex.fetch_balance()
        return float(bal["total"].get(self.quote) or 0.0)

    def set_leverage(self, symbol, leverage):
        if self.is_spot or self.mode == "paper":
            return
        try:
            self.ex.set_leverage(leverage, self._trade_sym(symbol))
        except Exception as e:  # z.B. bereits gesetzt
            print("set_leverage:", e)

    # ---- Limit-Orders (Maker = niedrigere Gebuehr) ---------------------------
    def use_limit(self):
        """Limit statt Markt: nur Futures (Bybit-EU-Spot kostet Maker und Taker gleich viel)."""
        return self.cfg.get("order_type") == "limit" and not self.is_spot

    def _limit_then_market(self, symbol, buy, amount, params=None):
        """Post-only Limit zum besten Geld-/Briefkurs. Nicht (ganz) gefuellt nach limit_wait_s Sekunden ->
        Rest per Markt-Order. Gibt (Durchschnittspreis, Menge, Maker-Anteil 0..1) zurueck, None = gleich Markt."""
        wait = float(self.cfg.get("limit_wait_s", 15))
        t = self.public.fetch_ticker(self._trade_sym(symbol))
        last = float(t["last"])
        px = float((t.get("bid") if buy else t.get("ask")) or last)
        if self.mode == "paper":
            t0 = time.time()
            while time.time() - t0 < wait:      # gefuellt, sobald jemand zu unserem Preis handelt
                p = self.price(symbol)
                if (buy and p <= px) or (not buy and p >= px):
                    return px, amount, 1.0
                time.sleep(2)
            return self.price(symbol), amount, 0.0
        sym = self._trade_sym(symbol)
        side = "buy" if buy else "sell"
        lp = float(self.ex.price_to_precision(sym, px))
        try:
            o = self.ex.create_order(sym, "limit", side, amount, lp, {**(params or {}), "postOnly": True})
        except Exception:
            return None
        oid, filled, avg = o.get("id"), 0.0, lp
        t0 = time.time()
        while time.time() - t0 < wait:
            time.sleep(2)
            try:
                o = self.ex.fetch_order(oid, sym)
            except Exception:
                continue
            filled, avg = float(o.get("filled") or 0), float(o.get("average") or lp)
            if o.get("status") == "closed" or filled >= amount * 0.999:
                return avg, filled, 1.0
            if o.get("status") in ("canceled", "expired", "rejected"):
                break                                    # post-only abgelehnt (Kurs ist weggelaufen)
        try:
            self.ex.cancel_order(oid, sym)
        except Exception:
            pass
        try:
            o = self.ex.fetch_order(oid, sym)
            filled, avg = float(o.get("filled") or 0), float(o.get("average") or lp)
        except Exception:
            pass
        rest = float(self.ex.amount_to_precision(sym, max(0.0, amount - filled)))
        if rest <= 0:
            return avg, filled, 1.0
        m = self.ex.create_order(sym, "market", side, rest, None, params or {})
        mp = float(m.get("average") or m.get("price") or self.price(symbol))
        mf = float(m.get("filled") or rest)
        tot = filled + mf
        return (avg * filled + mp * mf) / tot, tot, filled / tot

    # ---- Orders -----------------------------------------------------------
    def open(self, symbol, side, amount, leverage):
        """Oeffnet Position. Gibt (Ausfuehrungspreis, tatsaechliche Menge) zurueck.
        self.last_maker = Anteil, der als Limit (Maker-Gebuehr) gefuellt wurde."""
        self.last_maker = 0.0
        if self.mode == "paper":
            if self.use_limit():
                px, amt, self.last_maker = self._limit_then_market(symbol, side == "long", amount)
                return px, amt
            return self.price(symbol), amount
        self.set_leverage(symbol, leverage)
        sym = self._trade_sym(symbol)
        amount = self._min_size(symbol, sym, amount)
        amount = float(self.ex.amount_to_precision(sym, amount))
        if self.use_limit():
            r = self._limit_then_market(symbol, side == "long", amount)
            if r:
                self.last_maker = r[2]
                return r[0], r[1]
        price = None
        if self.name == "bybiteu" and side == "long":
            # Bybit Spot: Market-Kauf wird in USDC/EUR-Betrag aufgegeben (Menge x Kurs)
            price = self.price(symbol)
        order = self.ex.create_order(sym, "market", "buy" if side == "long" else "sell", amount, price)
        if not order.get("filled") and order.get("id"):
            try:   # manche Boersen liefern Fuellmenge/Preis erst bei Nachfrage
                order = {**order, **{k: v for k, v in self.ex.fetch_order(order["id"], sym).items() if v}}
            except Exception:
                pass
        fill = float(order.get("average") or order.get("price") or self.price(symbol))
        filled = float(order.get("filled") or amount)
        return fill, filled

    def _min_size(self, symbol, sym, amount):
        """Kleine Konten: Ist die Order unter der Mindestgroesse der Boerse (z. B. 100 USDT bei BTC),
        wird sie auf die Mindestgroesse angehoben, aber hoechstens auf das Doppelte des geplanten Risikos."""
        try:
            lim = self.ex.market(sym).get("limits") or {}
            min_cost = float((lim.get("cost") or {}).get("min") or 0)
            min_amt = float((lim.get("amount") or {}).get("min") or 0)
        except Exception:
            return amount
        px = self.price(symbol)
        need = max(min_amt, min_cost / px if min_cost and px else 0) * 1.03
        if amount >= need:
            return amount
        if need > amount * 2:
            raise ValueError(f"Trade zu klein: {symbol} braucht mindestens {need * px:.0f} {self.quote} Positionsgroesse "
                             f"(geplant {amount * px:.0f}). Bei mehr Geld im Topf oder hoeherem Risiko geht es.")
        return need
    def close(self, symbol, side, amount, limit=False) -> float:
        """limit=True: erst Limit (Maker) versuchen – nur bei ruhigen Ausstiegen (Trend vorbei), nie bei Stops."""
        self.last_maker = 0.0
        use = limit and self.use_limit()
        if self.mode == "paper":
            if use:
                px, _, self.last_maker = self._limit_then_market(symbol, side != "long", amount)
                return px
            return self.price(symbol)
        self.cancel_stop(symbol)
        sym = self._trade_sym(symbol)
        amount = float(self.ex.amount_to_precision(sym, amount))
        params = {} if self.is_spot else {"reduceOnly": True}
        if use:
            r = self._limit_then_market(symbol, side != "long", amount, params)
            if r:
                self.last_maker = r[2]
                return r[0]
        if self.is_spot and side == "long":
            # Spot: Kaufgebuehr wird oft in der Coin abgezogen -> nur verkaufen, was wirklich da ist
            base = self.ex.market(sym)["base"]
            free = float((self.ex.fetch_balance().get(base) or {}).get("free") or 0)
            amount = min(amount, float(self.ex.amount_to_precision(sym, free)))
        order = self.ex.create_order(sym, "market", "sell" if side == "long" else "buy", amount, None, params)
        return float(order.get("average") or order.get("price") or self.price(symbol))

    # ---- Stop-Loss direkt an der Boerse (Futures) ---------------------------
    def supports_exchange_stop(self):
        return self.mode != "paper" and not self.is_spot

    def place_stop(self, symbol, side, amount, stop_price):
        """Setzt einen Stop-Market an der Boerse. Greift auch, wenn dein PC aus ist."""
        if not self.supports_exchange_stop():
            return None
        sym = self._trade_sym(symbol)
        close_side = "sell" if side == "long" else "buy"
        amount = float(self.ex.amount_to_precision(sym, amount))
        stop_price = float(self.ex.price_to_precision(sym, stop_price))
        if self.name == "krakenfutures":
            o = self.ex.create_order(sym, "market", close_side, amount, None,
                                     {"stopLossPrice": stop_price, "reduceOnly": True, "triggerSignal": "mark"})
            return o.get("id")
        try:
            o = self.ex.create_order(sym, "market", close_side, amount, None,
                                     {"stopLossPrice": stop_price, "reduceOnly": True, "workingType": "MARK_PRICE"})
        except Exception:
            o = self.ex.create_order(sym, "STOP_MARKET", close_side, amount, None,
                                     {"stopPrice": stop_price, "reduceOnly": True, "workingType": "MARK_PRICE"})
        return o.get("id")

    def cancel_stop(self, symbol):
        if not self.supports_exchange_stop():
            return
        sym = self._trade_sym(symbol)
        for params in ({}, {"trigger": True}):
            try:
                self.ex.cancel_all_orders(sym, params)
            except Exception:
                pass

    def open_position_sizes(self):
        """Live/Testnet Futures: {symbol: menge} aller offenen Positionen an der Boerse."""
        if not self.supports_exchange_stop():
            return None
        out = {}
        for p in self.ex.fetch_positions():
            amt = abs(float(p.get("contracts") or 0))
            if amt > 0:
                m = self.ex.markets.get(p["symbol"], {})
                out[f"{m.get('base', p['symbol'].split('/')[0])}/{self.quote}"] = amt
        return out

    # ---- Abgleich mit der Boerse (was ist WIRKLICH passiert?) --------------
    @staticmethod
    def _ms(iso):
        return int(datetime.fromisoformat(iso).timestamp() * 1000)

    def close_fill(self, symbol, side, opened_iso, closed_iso=None):
        """Echter Ausstiegspreis eines Trades laut Boerse (Durchschnitt der Schliess-Fills).
        Wichtig, wenn der Stop an der Boerse ausgeloest hat: Dann kennt der Bot den Preis sonst nicht.
        Gibt {"price", "amount", "fee"} oder None zurueck."""
        if self.mode == "paper" or self.ex is None:
            return None
        try:
            since = self._ms(opened_iso)
            until = self._ms(closed_iso) + 120_000 if closed_iso else None
            fills = self.ex.fetch_my_trades(self._trade_sym(symbol), since=since, limit=200)
        except Exception:
            return None
        close_side = "sell" if side == "long" else "buy"
        sel = [f for f in fills if f.get("side") == close_side and f.get("timestamp")
               and (until is None or f["timestamp"] <= until)]
        qty = sum(float(f.get("amount") or 0) for f in sel)
        if qty <= 0:
            return None
        avg = sum(float(f["price"]) * float(f["amount"]) for f in sel) / qty
        fee = sum(float((f.get("fee") or {}).get("cost") or 0) for f in sel)
        return {"price": avg, "amount": qty, "fee": fee}

    def exchange_pnl(self, since_iso):
        """Binance-Futures: echte Summe seit since_iso laut Boerse, aufgeteilt nach
        realisiertem Gewinn, Gebuehren und Funding. None, wenn nicht verfuegbar."""
        if self.name != "binance" or self.is_spot or self.mode == "paper" or self.ex is None:
            return None
        out, start = {}, self._ms(since_iso)
        try:
            for _ in range(20):
                rows = self.ex.fapiPrivateGetIncome({"startTime": start, "limit": 1000})
                for r in rows:
                    k = r.get("incomeType", "OTHER")
                    out[k] = out.get(k, 0.0) + float(r.get("income") or 0)
                if len(rows) < 1000:
                    break
                start = int(rows[-1]["time"]) + 1
        except Exception:
            return None
        return {"realized": round(out.get("REALIZED_PNL", 0.0), 2),
                "fees": round(out.get("COMMISSION", 0.0), 2),
                "funding": round(out.get("FUNDING_FEE", 0.0), 2),
                "total": round(sum(v for k, v in out.items() if k != "TRANSFER"), 2)}

    def book_paper_pnl(self, pnl):
        if self.mode == "paper":
            self.paper_balance += pnl
