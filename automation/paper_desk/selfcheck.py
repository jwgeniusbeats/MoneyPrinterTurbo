"""Offline checks of the paper accounts and the pipeline. Run: python3 automation/paper_desk/selfcheck.py"""
import json
import math
import os
import random
import sys
import tempfile

os.environ["DESK_STUB"] = "1"
os.environ["DESK_DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from automation.paper_desk import desk, score  # noqa: E402


def candles(n=400, seed=1, start=60000.0, drift=0.0):
    rng, p, out, t = random.Random(seed), start, [], 1_700_000_000
    for _ in range(n):
        o = p; p *= 1 + drift + rng.gauss(0, 0.004)
        out.append({"t": t, "o": o, "h": max(o, p) * 1.001, "l": min(o, p) * 0.999, "c": p, "v": 100 + rng.random() * 50}); t += 3600
    return out


def test_accounting():
    a = {"pos": 0.0, "entry": 100.0, "equity": 10000.0, "day_start_equity": 10000.0, "day": None, "n_trades": 0, "fees": 0.0}
    desk.step_arm(a, 100.0, 1.0, 1_700_000_000)                       # open long: pays fee+slippage once
    cost = 10000 * (desk.FEE + desk.SLIP)
    assert abs(a["equity"] - (10000 - cost)) < 1e-6 and a["n_trades"] == 1
    desk.step_arm(a, 110.0, 1.0, 1_700_003_600)                       # +10% mark to market, no trade, no fee
    assert abs(a["equity"] - (10000 - cost) * 1.10) < 1e-6 and a["n_trades"] == 1
    e = a["equity"]; desk.step_arm(a, 110.0, -1.0, 1_700_007_200)     # flip long -> short: change of 2 => 2x cost
    assert abs(a["equity"] - (e - 2 * e * (desk.FEE + desk.SLIP))) < 1e-6 and a["n_trades"] == 2


def test_titan():
    f = {"atr14_pct": 2.0}
    assert desk.titan(1, f, 10000, 10000)[0] == 0.5
    assert desk.titan(1, {"atr14_pct": 0.5}, 10000, 10000)[0] == 1.0
    assert desk.titan(1, {"atr14_pct": 0.5}, 10000, 9600)[0] == 0.0   # -4% on the day: flat


def test_parse():
    assert desk.parse_decision('x {"position": 1, "confidence": 0.7, "reason": "r"} y')[0] == 1
    assert desk.parse_decision("no json")[3] is not None               # falls back to flat and flags the error
    assert desk.parse_decision('{"position": 5}')[0] == 0


def test_cycles_and_score():
    c = candles(); ex = {"funding_rate": 0.0001, "oi_change_24h_pct": 1.0, "fear_greed": 50, "fear_greed_label": "Neutral"}
    state = desk.new_state(c[-1]["c"], 1_700_000_000); rows = []
    for i in range(30):
        cc = candles(n=400 + i, seed=1)[: 400 + i]
        desk.run_cycle(state, cc, ex, 1_700_000_000 + i * 4 * 3600, lambda k, r: rows.append((k, r)))
    eq = [r for k, r in rows if k == "equity"]
    assert len(eq) == 30 * len(desk.ARMS) and all(math.isfinite(r["equity"]) and r["equity"] > 0 for r in eq)
    dec = [r for k, r in rows if k == "decisions" and r["arm"] == "desk"]
    assert dec[0]["facts"]["symbol"] and all(r["reason"] is not None for r in dec)
    sp, logs = desk._paths()
    for k, r in rows:
        open(logs[k], "a").write(json.dumps(r) + "\n")
    json.dump(state, open(sp, "w"))
    rep = score.report()
    assert "Too early" in rep["verdict"] and set(rep["arms"]) == set(desk.ARMS)   # must refuse to conclude on 5 days


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok", name)
