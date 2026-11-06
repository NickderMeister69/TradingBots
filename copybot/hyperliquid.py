"""Lesender Zugriff auf Hyperliquid. Es werden nur öffentliche Daten abgefragt, kein Konto, kein API-Schlüssel."""
import asyncio
import json
import time

import httpx

from config import HL_INFO_URL, HL_LEADERBOARD_URL, HTTP_CONCURRENCY


class HLError(Exception):
    pass


class Hyperliquid:
    def __init__(self):
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0),
                                        headers={"Content-Type": "application/json"})
        self.sem = asyncio.Semaphore(HTTP_CONCURRENCY)
        self.last_latency_ms = None
        self.calls = 0
        self.errors = 0

    async def close(self):
        await self.client.aclose()

    async def info(self, payload, retries=3):
        delay = 1.0
        for attempt in range(retries):
            async with self.sem:
                t0 = time.perf_counter()
                try:
                    r = await self.client.post(HL_INFO_URL, content=json.dumps(payload))
                    self.calls += 1
                    self.last_latency_ms = round((time.perf_counter() - t0) * 1000)
                    if r.status_code == 429:
                        raise HLError("Zu viele Anfragen (429)")
                    r.raise_for_status()
                    return r.json()
                except (httpx.HTTPError, HLError, ValueError) as e:
                    self.errors += 1
                    if attempt == retries - 1:
                        raise HLError(f"{payload.get('type')}: {e}") from e
            await asyncio.sleep(delay)
            delay *= 2.5

    async def market(self):
        """Kurse, Funding und Coin-Liste der Perps."""
        meta, ctxs = await self.info({"type": "metaAndAssetCtxs"})
        out = {}
        for asset, ctx in zip(meta["universe"], ctxs):
            if asset.get("isDelisted"):
                continue
            mid = ctx.get("midPx") or ctx.get("markPx")
            if not mid:
                continue
            out[asset["name"]] = {
                "mid": float(mid),
                "mark": float(ctx["markPx"]),
                "funding": float(ctx.get("funding") or 0.0),   # Rate pro Stunde
                "prev_day": float(ctx.get("prevDayPx") or 0.0) or None,
                "max_lev": asset.get("maxLeverage"),
                "day_vlm": float(ctx.get("dayNtlVlm") or 0.0),
            }
        return out

    async def positions(self, address):
        st = await self.info({"type": "clearinghouseState", "user": address})
        av = float(st["marginSummary"]["accountValue"])
        pos = {}
        for ap in st.get("assetPositions", []):
            p = ap["position"]
            szi = float(p["szi"])
            if szi == 0:
                continue
            pos[p["coin"]] = {
                "szi": szi,
                "entry": float(p.get("entryPx") or 0),
                "value": float(p.get("positionValue") or 0),
                "upnl": float(p.get("unrealizedPnl") or 0),
                "lev": (p.get("leverage") or {}).get("value"),
            }
        return {"account": av, "positions": pos}

    async def portfolio_history(self, address):
        data = await self.info({"type": "portfolio", "user": address})
        d = dict(data)
        m = d.get("perpMonth") or d.get("month") or {}
        return [[int(t), float(v)] for t, v in m.get("accountValueHistory", [])]

    async def leaderboard(self):
        r = await self.client.get(HL_LEADERBOARD_URL, timeout=httpx.Timeout(120.0, connect=15.0))
        r.raise_for_status()
        return r.json()["leaderboardRows"]
