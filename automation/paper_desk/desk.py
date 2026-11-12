"""Paper-trading experiment: does a multi-agent LLM "desk" beat simple baselines?

PAPER ONLY. This module never places an order and never touches an exchange account.
It reads public market data, asks the models for a target position, and books the result
in a simulated account (fees and slippage included). Every decision is logged with a
timestamp so the result can be audited later.

Arms (all start with the same 10,000 USDT on the same symbol, same prices, same costs):
  buyhold     always long 1x
  random      random target in {-1, 0, +1}, seeded by timestamp
  ema         long when EMA20 > EMA50, else short
  single_llm  one model call that sees all the facts
  desk        4 analyst calls (Atlas scan, Vega technicals, Orion derivatives, Luna sentiment)
              -> Nova final call -> Titan (code, not a model) sizes / vetoes

Run one cycle:   python3 -m automation.paper_desk.desk run
Offline smoke:   DESK_STUB=1 python3 -m automation.paper_desk.desk run
Score:           python3 -m automation.paper_desk.score
"""
import json
import math
import os
import random
import sys
import urllib.request
from datetime import datetime, timezone

DATA_DIR = os.environ.get("DESK_DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
SYMBOL = os.environ.get("DESK_SYMBOL", "BTCUSDT")
MODEL = os.environ.get("DESK_MODEL", "claude-haiku-5-5")
START_EQUITY = 10000.0
FEE = 0.00055   # taker fee per side
SLIP = 0.0002   # assumed slippage per side
MIN_GAP_S = 3 * 3600  # refuse to run twice within 3 hours
ARMS = ["buyhold", "random", "ema", "single_llm", "desk"]
UA = {"User-Agent": "paper-desk/1.0"}


# ---------- data ----------
def _get_json(url, headers=None, data=None, timeout=25):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})}, data=data)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def fetch_candles(symbol=SYMBOL, interval="60", limit=200):
    j = _get_json(f"https://api.bybit.com/v5/market/kline?category=linear&symbol={symbol}&interval={interval}&limit={limit}")
    rows = j["result"]["list"][::-1]  # oldest first
    return [{"t": int(r[0]) // 1000, "o": float(r[1]), "h": float(r[2]), "l": float(r[3]), "c": float(r[4]), "v": float(r[5])} for r in rows]


def fetch_extras(symbol=SYMBOL):
    out = {}
    try:
        t = _get_json(f"https://api.bybit.com/v5/market/tickers?category=linear&symbol={symbol}")["result"]["list"][0]
        out["funding_rate"] = float(t.get("fundingRate") or 0)
    except Exception as e:
        out["funding_rate"] = None
    try:
        oi = _get_json(f"https://api.bybit.com/v5/market/open-interest?category=linear&symbol={symbol}&intervalTime=1h&limit=25")["result"]["list"]
        a, b = float(oi[0]["openInterest"]), float(oi[-1]["openInterest"])  # newest first
        out["oi_change_24h_pct"] = (a / b - 1) * 100 if b else None
    except Exception:
        out["oi_change_24h_pct"] = None
    try:
        f = _get_json("https://api.alternative.me/fng/?limit=1")["data"][0]
        out["fear_greed"] = int(f["value"]); out["fear_greed_label"] = f["value_classification"]
    except Exception:
        out["fear_greed"] = None; out["fear_greed_label"] = None
    return out


# ---------- indicators (all numbers are computed in code, never by a model) ----------
def ema(vals, n):
    k = 2 / (n + 1); e = vals[0]
    for v in vals[1:]:
        e = v * k + e * (1 - k)
    return e


def rsi(closes, n=14):
    gains = losses = 0.0
    for i in range(len(closes) - n, len(closes)):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0); losses += max(-d, 0)
    if losses == 0:
        return 100.0
    rs = (gains / n) / (losses / n)
    return 100 - 100 / (1 + rs)


def atr_pct(c, n=14):
    trs = []
    for i in range(len(c) - n, len(c)):
        trs.append(max(c[i]["h"] - c[i]["l"], abs(c[i]["h"] - c[i - 1]["c"]), abs(c[i]["l"] - c[i - 1]["c"])))
    return sum(trs) / n / c[-1]["c"] * 100


def build_facts(candles, extras):
    closes = [x["c"] for x in candles]
    last = candles[-1]
    prev24 = candles[-25]["c"]
    vol_now = sum(x["v"] for x in candles[-24:])
    vol_base = sum(x["v"] for x in candles[-24 - 168:-24]) / 7 if len(candles) >= 24 + 168 else None
    return {
        "symbol": SYMBOL, "price": last["c"], "candle_time_utc": datetime.fromtimestamp(last["t"], timezone.utc).isoformat(),
        "change_24h_pct": round((last["c"] / prev24 - 1) * 100, 2),
        "volume_vs_7d_avg": round(vol_now / vol_base, 2) if vol_base else None,
        "rsi14": round(rsi(closes), 1), "ema20": round(ema(closes[-60:], 20), 2), "ema50": round(ema(closes[-100:], 50), 2),
        "atr14_pct": round(atr_pct(candles), 2), **extras,
    }


# ---------- models ----------
def call_model(system, user, max_tokens=300):
    if os.environ.get("DESK_STUB") or not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    body = json.dumps({"model": MODEL, "max_tokens": max_tokens, "system": system, "messages": [{"role": "user", "content": user}]}).encode()
    j = _get_json("https://api.anthropic.com/v1/messages", headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01", "content-type": "application/json"}, data=body, timeout=60)
    return "".join(b.get("text", "") for b in j.get("content", []))


ROLE = {
    "Atlas": "You are ATLAS, the market scanner. From the facts, say in 2 sentences whether price action and volume look orderly, stretched or unusual. End with one word: BULLISH, BEARISH or NEUTRAL.",
    "Vega": "You are VEGA, the chart analyst. From the technical facts (RSI, EMA20/50, ATR), say in 2 sentences what the trend and momentum are. End with one word: BULLISH, BEARISH or NEUTRAL.",
    "Orion": "You are ORION, the derivatives watcher (funding rate and open interest, no news feed). In 2 sentences say what positioning implies. End with one word: BULLISH, BEARISH or NEUTRAL.",
    "Luna": "You are LUNA, the sentiment reader (Fear and Greed index only). In 2 sentences say what sentiment implies; remember extremes can be contrarian. End with one word: BULLISH, BEARISH or NEUTRAL.",
}
DECIDE = ("Reply with ONLY a JSON object: {\"position\": -1 or 0 or 1, \"confidence\": number 0 to 1, \"reason\": \"one sentence\"}. "
          "position 1 = long, -1 = short, 0 = flat for the next 4 hours. Staying flat is a valid and often correct answer. Never invent numbers.")


def parse_decision(text):
    try:
        a, b = text.index("{"), text.rindex("}")
        d = json.loads(text[a:b + 1])
        p = int(d.get("position", 0))
        return (p if p in (-1, 0, 1) else 0), float(d.get("confidence", 0)), str(d.get("reason", ""))[:300], None
    except Exception as e:
        return 0, 0.0, "", f"parse_error: {e}"


def stub_decision(facts):  # only for offline smoke tests; clearly labelled in the log
    p = 1 if facts["ema20"] > facts["ema50"] and facts["rsi14"] < 70 else (-1 if facts["ema20"] < facts["ema50"] and facts["rsi14"] > 30 else 0)
    return p, 0.5, "stub decision (no model call)", None


def titan(position, facts, equity_today_start, equity_now):
    """Risk officer. Plain code. Scales down in high volatility, goes flat after a bad day."""
    size = float(position)
    notes = []
    if facts["atr14_pct"] > 1.2:
        size *= 0.5; notes.append("atr>1.2%: half size")
    if equity_today_start and equity_now / equity_today_start - 1 < -0.03:
        size = 0.0; notes.append("daily loss limit hit: flat")
    return size, "; ".join(notes)


def desk_decision(facts):
    reports, text = {}, json.dumps(facts, ensure_ascii=False)
    for name, system in ROLE.items():
        reports[name] = call_model(system, text)
    if all(v is None for v in reports.values()):
        p, c, r, err = stub_decision(facts)
        return p, c, r, err, {"mode": "stub"}
    brief = "\n".join(f"{k}: {v}" for k, v in reports.items())
    nova = call_model("You are NOVA, the final decision maker of a paper trading desk. You get the raw facts and four analyst reports. " + DECIDE, f"FACTS:\n{text}\n\nREPORTS:\n{brief}")
    p, c, r, err = parse_decision(nova or "")
    return p, c, r, err, {"reports": reports, "nova_raw": nova}


def single_decision(facts):
    out = call_model("You are a crypto trader on a paper account. " + DECIDE, json.dumps(facts, ensure_ascii=False))
    if out is None:
        p, c, r, err = stub_decision(facts)
        return p, c, r, err, {"mode": "stub"}
    p, c, r, err = parse_decision(out)
    return p, c, r, err, {"raw": out}


# ---------- paper accounts ----------
def new_state(price, ts):
    return {"started": ts, "last_run": None, "start_price": price,
            "arms": {a: {"pos": 0.0, "entry": price, "equity": START_EQUITY, "day_start_equity": START_EQUITY, "day": None, "n_trades": 0, "fees": 0.0} for a in ARMS}}


def step_arm(arm, price, target, ts):
    """Mark to market at `price`, then move to `target` paying fee + slippage on the change."""
    day = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")
    if arm["day"] != day:
        arm["day"] = day; arm["day_start_equity"] = arm["equity"]
    arm["equity"] *= 1 + arm["pos"] * (price / arm["entry"] - 1)
    arm["entry"] = price
    change = abs(target - arm["pos"])
    if change > 1e-9:
        cost = change * arm["equity"] * (FEE + SLIP)
        arm["equity"] -= cost; arm["fees"] += cost; arm["n_trades"] += 1
        arm["pos"] = target
    return arm


def run_cycle(state, candles, extras, ts, log):
    facts = build_facts(candles, extras)
    price = facts["price"]
    rng = random.Random(ts // 3600)
    decisions = {
        "buyhold": (1.0, "always long", None, {}),
        "random": (float(rng.choice([-1, 0, 1])), "random", None, {}),
        "ema": (1.0 if facts["ema20"] > facts["ema50"] else -1.0, "ema20 vs ema50", None, {}),
    }
    p, c, r, err, aux = single_decision(facts); decisions["single_llm"] = (float(p), r, err, {**aux, "confidence": c})
    p, c, r, err, aux = desk_decision(facts)
    a = state["arms"]["desk"]
    sized, tnote = titan(p, facts, a["day_start_equity"], a["equity"] * (1 + a["pos"] * (price / a["entry"] - 1)))
    decisions["desk"] = (sized, r + (f" [Titan: {tnote}]" if tnote else ""), err, {**aux, "nova_position": p, "confidence": c})
    for name in ARMS:
        target, reason, err, aux = decisions[name]
        before = state["arms"][name]["pos"]
        step_arm(state["arms"][name], price, target, ts)
        log("decisions", {"ts": ts, "iso": datetime.fromtimestamp(ts, timezone.utc).isoformat(), "arm": name, "price": price, "from": before, "to": target, "reason": reason, "error": err, "aux": aux, "facts": facts if name == "desk" else None})
        log("equity", {"ts": ts, "arm": name, "equity": round(state["arms"][name]["equity"], 4), "price": price, "pos": state["arms"][name]["pos"]})
    state["last_run"] = ts
    return state


# ---------- io ----------
def _paths():
    os.makedirs(DATA_DIR, exist_ok=True)
    return os.path.join(DATA_DIR, "state.json"), {"decisions": os.path.join(DATA_DIR, "decisions.jsonl"), "equity": os.path.join(DATA_DIR, "equity.jsonl")}


def main(argv=None):
    argv = argv or sys.argv[1:]
    if argv and argv[0] == "check":  # fetch + compute facts, write nothing
        facts = build_facts(fetch_candles(), fetch_extras())
        print(json.dumps(facts, indent=1)); print("model key set:", bool(os.environ.get("ANTHROPIC_API_KEY")) and not os.environ.get("DESK_STUB")); return 0
    if not argv or argv[0] != "run":
        print(__doc__); return 2
    state_path, logs = _paths()
    ts = int(datetime.now(timezone.utc).timestamp())
    state = json.load(open(state_path)) if os.path.exists(state_path) else None
    if state and state.get("last_run") and ts - state["last_run"] < MIN_GAP_S and "--force" not in argv:
        print("Skipped: last cycle was less than 3 hours ago."); return 0
    candles = fetch_candles(); extras = fetch_extras()
    if state is None:
        state = new_state(candles[-1]["c"], ts)

    def log(kind, row):
        with open(logs[kind], "a") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    run_cycle(state, candles, extras, ts, log)
    tmp = state_path + ".tmp"
    json.dump(state, open(tmp, "w"), indent=1); os.replace(tmp, state_path)
    print("Cycle done:", {k: round(v["equity"], 2) for k, v in state["arms"].items()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
