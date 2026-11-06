import yaml
from bot.engine import Engine, PRESET_VERSION
def test_gold_config_valid():
    c = yaml.safe_load(open("config_gold.yaml"))
    assert c["dashboard_port"] == 8019 and c["timeframe"] == "1d" and c["symbols"] == ["PAXG/USDT"]
    assert c["risk_per_trade"] == 0.03 and c["pyramid"]["max_units"] == 2 and c["btc_filter"] is False
    assert c["mode"] == "paper" and c["preset_version"] <= PRESET_VERSION
    e = Engine.__new__(Engine)
    for k, (typ, lo, hi) in Engine.EDITABLE.items():
        if k in c:
            assert lo <= typ(c[k]) <= hi, k
