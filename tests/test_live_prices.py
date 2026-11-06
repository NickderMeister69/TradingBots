import asyncio, sys, types
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "copybot"))
import engine as E

def test_live_prices_updates_without_touching_prev_market():
    class HL:
        async def info(self, payload, retries=3):
            assert payload == {"type": "allMids"}
            return {"BTC": "101", "ETH": "2001"}
    old = {"BTC": {"mid": 100.0, "mark": 100.0, "funding": 0.0001}, "SOL": {"mid": 5.0, "mark": 5.0, "funding": 0.0}}
    o = types.SimpleNamespace(market=old, prev_market=old, lock=asyncio.Lock(), hl=HL())
    async def go():
        t = asyncio.create_task(E.Engine._live_prices(o))
        await asyncio.sleep(2.3)
        t.cancel()
    asyncio.run(go())
    assert o.market["BTC"]["mark"] == 101.0 and o.market["BTC"]["funding"] == 0.0001
    assert o.market["SOL"]["mark"] == 5.0
    assert o.prev_market["BTC"]["mark"] == 100.0      # Lern-Daten bleiben unberuehrt
