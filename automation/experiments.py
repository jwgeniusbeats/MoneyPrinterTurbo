"""Zelflerende A/B-tests voor de video's: de pijplijn kiest zelf varianten, meet na een paar dagen welke wint
en verschuift automatisch meer video's naar de winnaar. Geen handwerk nodig.

Hoe het werkt
  * EXPERIMENTS beschrijft per variabele de varianten ("arms"). Per video wordt per variabele één variant gekozen,
    gewogen met automation/experiment_state.json (begin: 50/50).
  * De gekozen varianten staan per video in post_log.json onder "experiment".
  * Na elke run (update_from_post_log) krijgt elke rijpe video (>= MIN_AGE_DAYS oud) een beloning: het percentiel van
    zijn totaal aantal views over alle platforms, gemiddeld met het percentiel van YouTube-kijkpercentage (als er
    >= 10 views zijn). Percentielen, zodat verschillende schalen nooit door elkaar lopen.
  * Een bootstrap geeft per variant de kans dat hij de beste is. Pas vanaf MIN_SAMPLES_PER_ARM video's per variant
    gaan de gewichten bewegen, en geen enkele variant komt onder de vloer (EXPLORATION_FLOOR), zodat er blijft
    worden getest en een toevalstreffer niet blijft hangen.
  * Alleen vooraf goedgekeurde varianten; het systeem verzint zelf geen nieuwe tekst.

Bij deze aantallen views (rond 240 per video op TikTok) is ruis groot: kleine verschillen zie je pas na ~20 video's
per variant. Het rapport zegt dat eerlijk (automation/experiment_report.md).
"""
import json
import os
import random
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_FILE = os.path.join(BASE_DIR, "automation", "experiment_state.json")
REPORT_FILE = os.path.join(BASE_DIR, "automation", "experiment_report.md")

# Per variabele: variant -> tekst die in de scriptprompt komt (of None als het alleen een instelling is).
EXPERIMENTS = {
    "length": {
        "short": "Write 45-55 words.",
        "long": "Write 70-90 words.",
    },
    "subtitles": {
        "sentence": None,
        "word_by_word": None,
    },
}
DEFAULT_LENGTH_INSTRUCTION = "Write 70-90 words."

MIN_AGE_DAYS = 3
MIN_SAMPLES_PER_ARM = 8
EXPLORATION_FLOOR = 0.2
BOOTSTRAP_ROUNDS = 2000
MIN_VIEWS_FOR_RETENTION = 10


def _load_state(path=None) -> dict:
    try:
        with open(path or STATE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_state(state: dict, path=None) -> None:
    path = path or STATE_FILE
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, path)


def weights_for(variable: str, state: dict) -> dict:
    arms = list(EXPERIMENTS[variable])
    saved = (state.get("variables", {}).get(variable, {}) or {}).get("weights") or {}
    w = {a: float(saved.get(a, 0)) for a in arms}
    total = sum(w.values())
    if total <= 0:
        return {a: 1.0 / len(arms) for a in arms}
    return {a: v / total for a, v in w.items()}


def choose_arms(rng=None, state=None) -> dict:
    """Kies per variabele een variant, gewogen. Valt terug op 50/50 als het statusbestand ontbreekt of stuk is."""
    rng = rng or random
    state = _load_state() if state is None else state
    chosen = {}
    for variable, arms in EXPERIMENTS.items():
        w = weights_for(variable, state)
        names = list(arms)
        chosen[variable] = rng.choices(names, weights=[w[n] for n in names], k=1)[0]
    return chosen


def length_instruction(arm) -> str:
    return (EXPERIMENTS["length"].get(arm) or DEFAULT_LENGTH_INSTRUCTION)


# --- Meten --------------------------------------------------------------------------------------------------

def _total_views(entry: dict) -> int:
    total = 0
    for p in (entry.get("platforms") or {}).values():
        v = p.get("views")
        if v is not None:
            total += int(v)
    return total


def _parse(ts):
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _is_mature(entry: dict, now=None) -> bool:
    t = _parse(entry.get("posted_at"))
    if t is None:
        return False
    return ((now or datetime.now(timezone.utc)) - t).days >= MIN_AGE_DAYS


def _percentiles(values: list) -> list:
    """Percentiel (0..1) per waarde; gelijke waarden krijgen het gemiddelde percentiel."""
    n = len(values)
    if n == 1:
        return [0.5]
    order = sorted(range(n), key=lambda i: values[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg / (n - 1)
        i = j + 1
    return ranks


def rewards(entries: list, now=None) -> list:
    """-> lijst van (entry, beloning 0..1) voor rijpe video's met experimentgegevens."""
    tagged = [e for e in entries if e.get("experiment") and _is_mature(e, now)]
    if not tagged:
        return []
    views = _percentiles([_total_views(e) for e in tagged])
    ret_idx = []
    ret_vals = []
    for i, e in enumerate(tagged):
        yt = (e.get("platforms") or {}).get("youtube") or {}
        pct = yt.get("avg_view_percentage")
        if pct and (yt.get("views") or 0) >= MIN_VIEWS_FOR_RETENTION:
            ret_idx.append(i)
            ret_vals.append(float(pct))
    ret_rank = dict(zip(ret_idx, _percentiles(ret_vals))) if ret_vals else {}
    out = []
    for i, e in enumerate(tagged):
        parts = [views[i]] + ([ret_rank[i]] if i in ret_rank else [])
        out.append((e, sum(parts) / len(parts)))
    return out


def _mean(a):
    return sum(a) / len(a)


def analyse(entries: list, rng=None, now=None) -> dict:
    """Per variabele: n en gemiddelde beloning per variant, kans-dat-beste en nieuwe gewichten."""
    rng = rng or random.Random(12345)
    scored = rewards(entries, now)
    result = {}
    for variable, arms in EXPERIMENTS.items():
        groups = {a: [] for a in arms}
        for e, r in scored:
            arm = (e.get("experiment") or {}).get(variable)
            if arm in groups:
                groups[arm].append(r)
        names = list(arms)
        stats = {a: {"n": len(groups[a]), "mean": (round(_mean(groups[a]), 3) if groups[a] else None)} for a in names}
        enough = all(len(groups[a]) >= MIN_SAMPLES_PER_ARM for a in names)
        p_best = {a: None for a in names}
        if enough:
            wins = {a: 0 for a in names}
            for _ in range(BOOTSTRAP_ROUNDS):
                means = {a: _mean([rng.choice(groups[a]) for _ in groups[a]]) for a in names}
                wins[max(means, key=means.get)] += 1
            p_best = {a: wins[a] / BOOTSTRAP_ROUNDS for a in names}
            raw = {a: max(EXPLORATION_FLOOR, p_best[a]) for a in names}
            total = sum(raw.values())
            weights = {a: raw[a] / total for a in names}
        else:
            weights = {a: 1.0 / len(names) for a in names}
        result[variable] = {"arms": stats, "p_best": p_best, "weights": weights, "enough_data": enough}
    return result


def _describe(variable: str, info: dict) -> str:
    lines = ["## " + variable]
    for a, s in info["arms"].items():
        pb = info["p_best"][a]
        lines.append(
            "- %s: %d video's met cijfers, gemiddelde beloning %s, kans dat dit de beste is %s, gewicht %.0f%%"
            % (a, s["n"], s["mean"] if s["mean"] is not None else "n/a",
               ("%.0f%%" % (pb * 100)) if pb is not None else "n/a",
               info["weights"][a] * 100)
        )
    if not info["enough_data"]:
        need = MIN_SAMPLES_PER_ARM
        lines.append("  Nog te weinig data (minstens %d rijpe video's per variant), dus blijft het 50/50." % need)
    else:
        best = max(info["p_best"], key=info["p_best"].get)
        lines.append("  Voorlopige winnaar: %s (kans %.0f%%). Een vloer van %d%% blijft over voor testen."
                     % (best, info["p_best"][best] * 100, EXPLORATION_FLOOR * 100))
    return "\n".join(lines)


def update_from_post_log(entries: list, state_path=None, report_path=None, now=None) -> dict:
    """Herbereken gewichten uit post_log, sla ze op en schrijf een leesbaar rapport. Gooit nooit door naar de pijplijn."""
    analysis = analyse(entries, now=now)
    state = _load_state(state_path)
    state.setdefault("variables", {})
    for variable, info in analysis.items():
        state["variables"][variable] = {"weights": info["weights"], "arms": info["arms"], "p_best": info["p_best"]}
    state["updated"] = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    _save_state(state, state_path)
    report = ["# Experimenten (automatisch bijgewerkt)", "", "Bijgewerkt: " + state["updated"], "",
              "Beloning = percentiel van het totaal aantal views (en YouTube-kijkpercentage waar beschikbaar) onder "
              "rijpe video's (>= %d dagen). Bij ~240 views per video is ruis groot; kleine verschillen vragen ~20 "
              "video's per variant." % MIN_AGE_DAYS, ""]
    report += [_describe(v, i) + "\n" for v, i in analysis.items()]
    path = report_path or REPORT_FILE
    with open(path, "w") as f:
        f.write("\n".join(report))
    return analysis
