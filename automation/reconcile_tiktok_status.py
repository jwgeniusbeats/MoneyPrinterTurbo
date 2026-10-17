#!/usr/bin/env python3
"""Zet de TikTok-status in post_log.json op "live" voor video's die tiktok-daily-post echt heeft gepost.

Bewijs = een regel in automation/tiktok_posted.log (de taak schrijft die pas na een bevestigde post).
SKIPPED-regels tellen NIET mee. Alleen entries die nu "pending_manual" zijn worden aangepast, en alleen
als het pad in de log aan precies één entry te koppelen is. Niets wordt gegokt.

  uv run python automation/reconcile_tiktok_status.py            # droog: laat alleen zien wat er zou gebeuren
  uv run python automation/reconcile_tiktok_status.py --apply    # schrijft naar post_log.json (onder de lock)

Daarna: de TikTok-cijfers opnieuw scrapen en update_tiktok_stats.py draaien, dan export_status.py.
Let op: update_tiktok_stats.py koppelt op VOLGORDE aan alle "live" entries. Video's die handmatig of als test
zijn gepost zonder regel in tiktok_posted.log blijven hier "pending_manual" en worden dus niet geteld.
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from automation.post_log_lock import locked_post_log  # noqa: E402

POSTED_LOG = os.path.join(BASE_DIR, "automation", "tiktok_posted.log")


def folder_key(path: str) -> str:
    """Mapnaam van de video, bevat het unieke task-id en overleeft een ander home-pad."""
    return os.path.basename(os.path.dirname(path or ""))


def parse_posted_log(lines):
    """-> lijst van (pad, tijdstempel of None) voor echt geposte video's, oud -> nieuw, zonder duplicaten."""
    seen, out = set(), []
    for line in lines:
        if "SKIPPED" in line:
            continue
        paths = [t for t in line.split() if t.startswith("/")]
        if not paths:
            continue
        ts = None
        first = line.split()[0] if line.split() else ""
        try:
            ts = datetime.fromisoformat(first.replace("Z", "+00:00")).isoformat()
        except ValueError:
            pass
        for p in paths:
            if p not in seen:
                seen.add(p)
                out.append((p, ts))
    return out


def plan(entries: list, posted: list):
    """Bepaal welke entries op live mogen. -> (updates[(index, ts, pad)], onbekend[pad], dubbelzinnig[pad])."""
    by_path, by_folder = {}, {}
    for i, e in enumerate(entries):
        vp = e.get("video_path")
        if not vp:
            continue
        by_path.setdefault(vp, []).append(i)
        by_folder.setdefault(folder_key(vp), []).append(i)
    updates, unknown, ambiguous = [], [], []
    for path, ts in posted:
        idx = by_path.get(path) or by_folder.get(folder_key(path)) or []
        if len(idx) == 0:
            unknown.append(path)
        elif len(idx) > 1:
            ambiguous.append(path)
        else:
            i = idx[0]
            status = entries[i].get("platforms", {}).get("tiktok", {}).get("status")
            if status == "pending_manual":
                updates.append((i, ts, path))
    return updates, unknown, ambiguous


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="schrijf naar post_log.json (standaard: droog)")
    args = ap.parse_args()

    with open(POSTED_LOG) as f:
        posted = parse_posted_log(f.readlines())

    with locked_post_log() as entries:
        updates, unknown, ambiguous = plan(entries, posted)
        live_now = sum(1 for e in entries if e.get("platforms", {}).get("tiktok", {}).get("status") == "live")
        print(f"Echt gepost volgens tiktok_posted.log (zonder SKIPPED): {len(posted)}")
        print(f"Nu al 'live' in post_log.json: {live_now}")
        print(f"Zou op live gezet worden: {len(updates)}")
        print(f"Pad zonder entry in post_log.json: {len(unknown)}")
        print(f"Pad dat aan meerdere entries past (overgeslagen): {len(ambiguous)}")
        for i, ts, path in updates[-10:]:
            print(f"  live <- {entries[i].get('title', '')[:50]!r}  ({ts or 'geen tijd in log'})")
        for p in unknown[-5:]:
            print(f"  GEEN MATCH: {p[-70:]}")
        if not args.apply:
            print("Droog. Voeg --apply toe om te schrijven.")
            # Niets gewijzigd: locked_post_log schrijft dezelfde inhoud terug.
            return
        now = datetime.now(timezone.utc).isoformat()
        for i, ts, _ in updates:
            tt = entries[i].setdefault("platforms", {}).setdefault("tiktok", {})
            tt["status"] = "live"
            tt.setdefault("posted_at", ts or now)
            tt["reconciled"] = True  # herkenbaar en terug te draaien: haal deze entries weer op pending_manual
        print(f"Geschreven: {len(updates)} entries op live gezet (veld 'reconciled': true).")


if __name__ == "__main__":
    main()
