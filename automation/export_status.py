"""Schrijft een opgeschoonde statusfeed (geen bijschriften, geen paden, geen tokens) en zet die op de
GitHub-branch `status-feed`, zonder je werkmap of je index aan te raken.

Gebruik (aan het eind van daily_pipeline.main, niet-fataal):
    from automation.export_status import export_and_push
    export_and_push()
"""
import base64
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POST_LOG_FILE = os.path.join(BASE_DIR, "automation", "post_log.json")
OUT_FILE = os.path.join(BASE_DIR, "automation", "public_status.json")
KEYS = {"youtube": "y", "tiktok": "t", "instagram": "i", "facebook": "f"}
STAT = {"live": "l", "scheduled": "s", "pending_manual": "p", "private": "v"}


def thumbnail(video_path: str, width: int = 150) -> str:
    """Eerste bruikbare beeld van de video als kleine JPEG (data-URI). Leeg als het niet lukt."""
    if not video_path or not os.path.exists(video_path):
        return ""
    try:
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "t.jpg")
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "1.0", "-i", video_path, "-frames:v", "1",
                            "-vf", f"scale={width}:-2", "-q:v", "9", out], check=True, timeout=60)
            with open(out, "rb") as f:
                return "data:image/jpeg;base64," + base64.b64encode(f.read()).decode()
    except Exception:
        return ""


def build_feed(post_log: list) -> dict:
    items = []
    totals = {k: {"views": 0, "likes": 0, "comments": 0, "videos": 0, "live": 0} for k in KEYS}
    for e in post_log:
        p = {}
        for name, v in (e.get("platforms") or {}).items():
            if name not in KEYS:
                continue
            status = v.get("status")
            p[KEYS[name]] = [STAT.get(status, "x"), v.get("views") or 0, v.get("likes") or 0, v.get("comments") or 0]
            t = totals[name]
            t["videos"] += 1
            if status == "live":
                t["live"] += 1
                t["views"] += v.get("views") or 0
                t["likes"] += v.get("likes") or 0
                t["comments"] += v.get("comments") or 0
        items.append({"t": (e.get("title") or "")[:90], "s": (e.get("subject") or "")[:140], "d": (e.get("posted_at") or "")[:10], "p": p, "_v": e.get("video_path") or ""})
    items.sort(key=lambda x: x["d"], reverse=True)
    for it in items[:3]:  # alleen de 3 nieuwste krijgen een foto (klein, ~5 KB per stuk)
        it["img"] = thumbnail(it["_v"])
    for it in items:
        it.pop("_v", None)
        if not it.get("img"):
            it.pop("img", None)
    return {"asOf": datetime.now(timezone.utc).isoformat(timespec="seconds"), "platforms": totals, "items": items}


def _git(*args, input=None):
    return subprocess.run(["git", "-C", BASE_DIR, *args], input=input, capture_output=True, text=True, check=True).stdout.strip()


def push_feed(path: str, remote: str = "origin", branch: str = "status-feed"):
    """Plumbing: blob -> tree -> commit zonder ouder -> force-push. Raakt werkmap en index niet."""
    blob = _git("hash-object", "-w", path)
    tree = _git("mktree", input=f"100644 blob {blob}\tpublic_status.json\n")
    commit = _git("commit-tree", tree, "-m", f"status feed {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    _git("push", "--force", remote, f"{commit}:refs/heads/{branch}")


def export_and_push(remote: str = "origin"):
    try:
        with open(POST_LOG_FILE) as f:
            feed = build_feed(json.load(f))
        tmp = OUT_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(feed, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, OUT_FILE)
        push_feed(OUT_FILE, remote)
        print(f"Statusfeed bijgewerkt ({len(feed['items'])} video's)")
    except Exception as e:  # nooit de pipeline breken
        print(f"Statusfeed mislukt (niet fataal): {e}", file=sys.stderr)


if __name__ == "__main__":
    export_and_push(*(sys.argv[1:2]))
