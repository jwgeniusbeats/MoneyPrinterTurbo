"""
Merges scraped TikTok view/like/comment counts into automation/post_log.json,
matching each scraped row to a post_log entry by CONTENT (caption + date),
never by position.

Usage:
    uv run python automation/update_tiktok_stats.py <rows.json>            # dry run
    uv run python automation/update_tiktok_stats.py <rows.json> --apply    # write

rows.json is a JSON array, one object per row of TikTok Studio's content list:
    {"caption": "<first ~200 chars of the caption>", "date": "16 okt, 20:05",
     "views": int|null, "likes": int|null, "comments": int|null}

Why content, not order: matching by position silently mis-attributes stats to
the wrong video as soon as one row is missed, duplicated or reordered (seen
2026-10-17: a one-position shift for the older videos). A row is only matched
when ALL of these hold, otherwise it is skipped and nothing is written for it:
  - its caption is >= MIN_SIMILARITY similar to one live post_log entry's
    description AND clearly better than the runner-up (MARGIN),
  - its date agrees with that entry's tiktok.posted_at (same time within
    TIME_TOLERANCE_MIN, or same calendar day when posted_at is date-only),
  - no other row claims the same entry.

Default is a dry run (prints per-video what would be written). With --apply it
first copies post_log.json to automation/post_log.backup-<date>_<time>.json,
then writes ONLY tiktok.views, tiktok.likes, tiktok.comments (plus
tiktok.stats_fetched_at, solely on entries that are actually written, so
export_status.py gets a correct "data as of" date).
"""
import difflib
import json
import os
import re
import shutil
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, "/Users/geniusbeats/MoneyPrinterTurbo")
from automation.post_log_lock import POST_LOG_FILE, locked_post_log  # noqa: E402

MIN_SIMILARITY = 0.90
MARGIN = 0.05
TIME_TOLERANCE_MIN = 15
CAPTION_CHARS = 100  # the TikTok caption diverges from the logged description after ~120 chars
LOCAL_TZ = ZoneInfo("Europe/Amsterdam")
NL_MONTHS = {
    "jan": 1, "feb": 2, "mrt": 3, "apr": 4, "mei": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "okt": 10, "nov": 11, "dec": 12,
}


def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())[:CAPTION_CHARS]


def parse_studio_date(text: str, now: datetime) -> datetime | None:
    """'16 okt, 20:05' (local time, no year) -> aware datetime in Europe/Amsterdam."""
    m = re.match(r"\s*(\d{1,2})\s+([a-z]{3})\w*\.?,?\s+(\d{1,2}):(\d{2})\s*$", (text or "").lower())
    if not m or m.group(2) not in NL_MONTHS:
        return None
    day, month, hour, minute = int(m.group(1)), NL_MONTHS[m.group(2)], int(m.group(3)), int(m.group(4))
    for year in (now.year, now.year - 1):
        try:
            candidate = datetime(year, month, day, hour, minute, tzinfo=LOCAL_TZ)
        except ValueError:
            return None
        if candidate <= now + timedelta(days=1):
            return candidate
    return None


def parse_posted_at(text: str) -> tuple[datetime, bool] | None:
    """tiktok.posted_at -> (aware datetime in local time, is_date_only)."""
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    date_only = dt.hour == 0 and dt.minute == 0 and dt.second == 0
    return dt.astimezone(LOCAL_TZ), date_only


def dates_agree(studio: datetime, posted: tuple[datetime, bool]) -> tuple[bool, str]:
    posted_dt, date_only = posted
    if date_only:
        # Only a calendar day is known; allow one day either way (timezone edge).
        ok = abs((studio.date() - posted_dt.date()).days) <= 1
        return ok, f"dag {posted_dt.date()} (alleen datum bekend)"
    delta = abs((studio - posted_dt).total_seconds()) / 60
    return delta <= TIME_TOLERANCE_MIN, f"{delta:.0f} min verschil"


def build_plan(rows: list, entries: list, now: datetime) -> list[dict]:
    live = [e for e in entries if (e.get("platforms", {}).get("tiktok") or {}).get("status") == "live"]
    norm_desc = [normalize(e.get("description") or e.get("title") or "") for e in live]
    plan = []
    for i, row in enumerate(rows):
        item = {"row": i, "date": row.get("date"), "caption": (row.get("caption") or "")[:50],
                "entry": None, "sim": None, "write": False, "reason": ""}
        plan.append(item)
        if row.get("views") is None:
            item["reason"] = "geen cijfers (nog in beoordeling)"
            continue
        if not row.get("caption") or not row.get("date"):
            item["reason"] = "bijschrift of datum ontbreekt in rij"
            continue
        norm_cap = normalize(row["caption"])
        scores = sorted(
            ((difflib.SequenceMatcher(None, norm_cap, nd).ratio(), j) for j, nd in enumerate(norm_desc)),
            reverse=True,
        )
        if not scores:
            item["reason"] = "geen live entries"
            continue
        best, j = scores[0]
        second = scores[1][0] if len(scores) > 1 else 0.0
        entry = live[j]
        item.update(entry=entry, sim=best)
        if best < MIN_SIMILARITY:
            item["reason"] = f"bijschrift te onzeker ({best:.2f} < {MIN_SIMILARITY})"
            continue
        if best - second < MARGIN:
            item["reason"] = f"niet uniek: tweede kandidaat {second:.2f} ligt te dicht bij {best:.2f}"
            continue
        studio_dt = parse_studio_date(row["date"], now)
        posted = parse_posted_at((entry["platforms"]["tiktok"]).get("posted_at"))
        if studio_dt is None or posted is None:
            item["reason"] = "datum niet te lezen"
            continue
        ok, note = dates_agree(studio_dt, posted)
        if not ok:
            item["reason"] = f"datum klopt niet: {note}"
            continue
        item["write"] = True
        item["reason"] = f"zeker (bijschrift {best:.2f}, {note})"
    claims = {}
    for item in plan:
        if item["write"]:
            claims.setdefault(item["entry"]["task_id"], []).append(item)
    for items in claims.values():
        if len(items) > 1:
            for item in items:
                item["write"] = False
                item["reason"] = f"dubbel: rij {[x['row'] for x in items]} claimen dezelfde video"
    return plan


FALLBACK_MIN_SIMILARITY = 0.5


def propose_caption_fallback(rows: list, entries: list, now: datetime) -> None:
    """READ-ONLY. For rows the primary rule skipped only because the caption
    was below MIN_SIMILARITY, show whether a second rule would accept them:
    caption >= FALLBACK_MIN_SIMILARITY, date within TIME_TOLERANCE_MIN of the
    candidate's posted_at, and NO other live entry posted within that window.
    Never writes; the user decides per row."""
    live = [e for e in entries if (e.get("platforms", {}).get("tiktok") or {}).get("status") == "live"]
    plan = build_plan(rows, entries, now)
    norm_desc = [normalize(e.get("description") or e.get("title") or "") for e in live]
    posted = [parse_posted_at((e["platforms"]["tiktok"]).get("posted_at")) for e in live]
    print(f"TWEEDE REGEL, DROGE RUN (niets geschreven) | bijschrift >= {FALLBACK_MIN_SIMILARITY}, "
          f"datum binnen {TIME_TOLERANCE_MIN} min, geen andere entry in dat venster")
    proposals = {}
    skipped_caption = [p for p in plan if p["reason"].startswith("bijschrift te onzeker")]
    for item in skipped_caption:
        row = rows[item["row"]]
        norm_cap = normalize(row["caption"])
        ranked = sorted(
            ((difflib.SequenceMatcher(None, norm_cap, nd).ratio(), j) for j, nd in enumerate(norm_desc)),
            reverse=True,
        )
        sim, j = ranked[0]
        cand = live[j]
        tt = cand["platforms"]["tiktok"]
        studio_dt = parse_studio_date(row["date"], now)
        verdict, why = "NIET", ""
        if sim < FALLBACK_MIN_SIMILARITY:
            why = f"bijschrift {sim:.2f} < {FALLBACK_MIN_SIMILARITY}"
        elif studio_dt is None or posted[j] is None:
            why = "datum niet te lezen"
        elif posted[j][1]:
            why = "posted_at heeft alleen een datum: tijd niet te beoordelen"
        else:
            delta = abs((studio_dt - posted[j][0]).total_seconds()) / 60
            others = [live[k]["task_id"][:8] for k in range(len(live)) if k != j and posted[k]
                      and not posted[k][1]
                      and abs((studio_dt - posted[k][0]).total_seconds()) / 60 <= TIME_TOLERANCE_MIN]
            if delta > TIME_TOLERANCE_MIN:
                why = f"datum {delta:.0f} min verschil (> {TIME_TOLERANCE_MIN})"
            elif others:
                why = f"andere entry in venster: {others}"
            else:
                verdict, why = "VOORSTEL", f"bijschrift {sim:.2f}, {delta:.0f} min verschil, geen andere entry"
        proposals[item["row"]] = (verdict, cand)
        change = (f"views {tt.get('views')}->{row['views']}, likes {tt.get('likes')}->{row['likes']}, "
                  f"comments {tt.get('comments')}->{row['comments']}")
        print(f"[{item['row']:>2}] {verdict:<8} {row['date']:<13} {cand['task_id'][:8]} "
              f"{(cand.get('title') or '')[:34]:<34} {why}")
        print(f"       studio: {row['caption'][:70]!r}")
        print(f"       log   : {(cand.get('description') or '')[:70]!r}")
        print(f"       zou worden: {change}")
    claimed = {}
    for r, (v, cand) in proposals.items():
        if v == "VOORSTEL":
            claimed.setdefault(cand["task_id"], []).append(r)
    for tid, rs in claimed.items():
        if len(rs) > 1:
            print(f"WAARSCHUWING: rijen {rs} claimen dezelfde video {tid[:8]}")
    n = sum(1 for v, _ in proposals.values() if v == "VOORSTEL")
    print(f"samenvatting tweede regel: {len(proposals)} rijen beoordeeld, {n} voorstel, "
          f"{len(proposals) - n} niet. Er is niets geschreven.")


def print_plan(plan: list[dict], rows: list, live_count: int, apply: bool):
    print(f"{'WORDT GESCHREVEN' if apply else 'DROGE RUN (niets geschreven)'} | rijen: {len(rows)} | live entries: {live_count}")
    for item in plan:
        r = rows[item["row"]]
        title = (item["entry"].get("title") or "")[:34] if item["entry"] else "-"
        tid = item["entry"]["task_id"][:8] if item["entry"] else "--------"
        if item["write"]:
            old = item["entry"]["platforms"]["tiktok"]
            change = (f"views {old.get('views')}->{r['views']}, likes {old.get('likes')}->{r['likes']}, "
                      f"comments {old.get('comments')}->{r['comments']}")
            print(f"[{item['row']:>2}] SCHRIJF {r['date']:<13} {tid} {title:<34} {change}")
        else:
            print(f"[{item['row']:>2}] SLA OVER {str(item['date']):<13} {tid} {title:<34} {item['reason']}")
    written = sum(1 for p in plan if p["write"])
    matched = {p["entry"]["task_id"] for p in plan if p["write"]}
    print(f"samenvatting: {written} zeker, {len(plan) - written} overgeslagen")
    return matched


def main():
    flags = {"--apply", "--propose-caption-fallback"}
    args = [a for a in sys.argv[1:] if a not in flags]
    apply = "--apply" in sys.argv[1:]
    propose = "--propose-caption-fallback" in sys.argv[1:]
    if len(args) != 1 or (apply and propose):
        print("Usage: update_tiktok_stats.py <rows.json> [--apply | --propose-caption-fallback]", file=sys.stderr)
        sys.exit(1)
    with open(args[0]) as f:
        rows = json.load(f)
    now = datetime.now(LOCAL_TZ)

    if propose:
        with open(POST_LOG_FILE) as f:
            propose_caption_fallback(rows, json.load(f), now)
        return

    if not apply:
        with open(POST_LOG_FILE) as f:
            entries = json.load(f)
        plan = build_plan(rows, entries, now)
        live = [e for e in entries if (e.get("platforms", {}).get("tiktok") or {}).get("status") == "live"]
        matched = print_plan(plan, rows, len(live), apply=False)
        missing = [e for e in live if e["task_id"] not in matched]
        print(f"live entries zonder zekere rij: {len(missing)}")
        return

    with locked_post_log() as entries:
        plan = build_plan(rows, entries, now)
        backup = os.path.join(
            os.path.dirname(POST_LOG_FILE), f"post_log.backup-{now.strftime('%Y-%m-%d_%H%M%S')}.json"
        )
        shutil.copy2(POST_LOG_FILE, backup)
        live = [e for e in entries if (e.get("platforms", {}).get("tiktok") or {}).get("status") == "live"]
        print_plan(plan, rows, len(live), apply=True)
        for item in plan:
            if item["write"]:
                tt = item["entry"]["platforms"]["tiktok"]
                r = rows[item["row"]]
                tt["views"], tt["likes"], tt["comments"] = r["views"], r["likes"], r["comments"]
                tt["stats_fetched_at"] = datetime.now(timezone.utc).isoformat()
    print(f"backup: {backup}")


if __name__ == "__main__":
    main()
