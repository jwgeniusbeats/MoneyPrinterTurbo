"""Score the paper-trading experiment. Reads data/equity.jsonl and data/state.json.

Honest by construction: it refuses to call a winner before 30 days and 100 decision cycles,
and even then one asset over one month cannot prove an edge. It reports the numbers and the gap to
the four baselines (buy and hold, random, EMA cross, single model call), nothing more.
"""
import json
import os
import sys
from collections import defaultdict

DATA_DIR = os.environ.get("DESK_DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
MIN_DAYS, MIN_CYCLES = 30, 100


def load():
    eq = defaultdict(list)
    p = os.path.join(DATA_DIR, "equity.jsonl")
    if not os.path.exists(p):
        return eq, None
    for line in open(p):
        r = json.loads(line); eq[r["arm"]].append((r["ts"], r["equity"]))
    sp = os.path.join(DATA_DIR, "state.json")
    return eq, (json.load(open(sp)) if os.path.exists(sp) else None)


def max_dd(vals):
    peak, dd = vals[0], 0.0
    for v in vals:
        peak = max(peak, v); dd = min(dd, v / peak - 1)
    return dd * 100


def report():
    eq, state = load()
    if not eq:
        return {"error": "no data yet"}
    rows = {}
    for arm, series in eq.items():
        vals = [v for _, v in series]
        rows[arm] = {"return_pct": round((vals[-1] / 10000.0 - 1) * 100, 2), "max_drawdown_pct": round(max_dd([10000.0] + vals), 2),
                     "cycles": len(vals), "trades": state["arms"][arm]["n_trades"] if state else None, "fees": round(state["arms"][arm]["fees"], 2) if state else None}
    any_series = next(iter(eq.values()))
    days = (any_series[-1][0] - any_series[0][0]) / 86400
    cycles = len(any_series)
    out = {"days": round(days, 1), "cycles": cycles, "arms": rows}
    base = [a for a in ("buyhold", "random", "ema", "single_llm") if a in rows]
    if "desk" in rows and base:
        out["desk_minus_baselines_pct"] = {a: round(rows["desk"]["return_pct"] - rows[a]["return_pct"], 2) for a in base}
    if days < MIN_DAYS or cycles < MIN_CYCLES:
        out["verdict"] = f"Too early: need {MIN_DAYS} days and {MIN_CYCLES} cycles, have {round(days, 1)} days and {cycles}. No conclusion."
    else:
        beats_all = all(rows["desk"]["return_pct"] > rows[a]["return_pct"] for a in base)
        out["verdict"] = ("Desk beat all four baselines in this window. That is NOT proof of an edge: one asset, one month, costs assumed. "
                          "Keep it on paper for at least three months and test out of sample before any real money." if beats_all else
                          "Desk did not beat every baseline. The extra agents added cost, not edge, in this window.")
    return out


if __name__ == "__main__":
    r = report()
    if "--json" in sys.argv:
        print(json.dumps(r, indent=1)); sys.exit(0)
    if "error" in r:
        print(r["error"]); sys.exit(0)
    print(f"{r['days']} days, {r['cycles']} cycles")
    print(f"{'arm':<12}{'return %':>10}{'max DD %':>10}{'trades':>8}{'fees':>9}")
    for a, v in r["arms"].items():
        print(f"{a:<12}{v['return_pct']:>10}{v['max_drawdown_pct']:>10}{str(v['trades']):>8}{str(v['fees']):>9}")
    if "desk_minus_baselines_pct" in r:
        print("desk minus baselines (pct points):", r["desk_minus_baselines_pct"])
    print(r["verdict"])
