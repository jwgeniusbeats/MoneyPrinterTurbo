#!/usr/bin/env python3
"""Controleert of het einde van een video niet is afgekapt, vóór er iets wordt geüpload.

Waarom: video's eindigden soms midden in de slotzin ("... follow for more quick"). De oorzaak kan op meerdere
plekken zitten (stem, samenstellen, outro), dus deze controle kijkt naar het resultaat in plaats van naar de oorzaak:

  T = lengte van de stem (storage/tasks/<id>/audio.mp3, de ruwe TTS)
  A = werkelijke lengte van het audiospoor in de eindvideo
  V = werkelijke lengte van het beeldspoor in de eindvideo
  E = geluidsniveau van de laatste 100 ms van de stem (een afgeronde zin sterft weg, een afgekapte niet)

Fout als A < T - 0.15 s, V < T - 0.15 s of E hoger is dan -32 dB.
Alleen ffmpeg nodig (ook op een Mac zonder ffprobe). Faalt de controle zelf, dan wordt er NIET geblokkeerd.

Diagnose van recente video's (alleen lezen):
  uv run python automation/video_tail_check.py --last 6
"""
import argparse
import glob
import os
import re
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FFMPEG = os.environ.get("FFMPEG", "ffmpeg")

TOLERANCE_SECONDS = 0.15
ABRUPT_END_DB = -32.0
LEVEL_WINDOW_SECONDS = 0.1


def _run(args, timeout=120):
    return subprocess.run([FFMPEG, "-nostdin", "-hide_banner"] + args, capture_output=True, text=True, timeout=timeout)


def decoded_duration(path, stream="a"):
    """Werkelijke lengte van een spoor, door het te decoderen (container-metadata kan afwijken). None bij fout."""
    r = _run(["-v", "info", "-stats", "-i", path, "-map", "0:" + stream + ":0", "-f", "null", "-"])
    times = re.findall(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr or "")
    if r.returncode != 0 or not times:
        return None
    h, m, s = times[-1]
    return int(h) * 3600 + int(m) * 60 + float(s)


def end_level_db(path, duration):
    """Gemiddeld niveau (dB) van de laatste LEVEL_WINDOW_SECONDS van een audiobestand. None bij fout."""
    start = max(0.0, duration - LEVEL_WINDOW_SECONDS)
    r = _run(["-v", "info", "-ss", "%.3f" % start, "-i", path, "-af", "volumedetect", "-f", "null", "-"])
    m = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?|-inf) dB", r.stderr or "")
    if not m:
        return None
    return -120.0 if m.group(1) == "-inf" else float(m.group(1))


def check_tail(voice_path, video_path):
    """-> (ok, problemen[list], metingen[dict]). ok is True als er niets mis is OF als een meting niet lukte."""
    metrics = {}
    problems = []
    t = decoded_duration(voice_path, "a")
    a = decoded_duration(video_path, "a")
    v = decoded_duration(video_path, "v")
    e = end_level_db(voice_path, t) if t else None
    metrics.update({"T": t, "A": a, "V": v, "E": e})
    if t is None:
        return True, ["geen meting van de stem, controle overgeslagen"], metrics
    if a is not None and a < t - TOLERANCE_SECONDS:
        problems.append("audio in de video is %.2fs korter dan de stem (afgekapt bij het samenstellen)" % (t - a))
    if v is not None and v < t - TOLERANCE_SECONDS:
        problems.append("beeld is %.2fs korter dan de stem" % (t - v))
    if e is not None and e > ABRUPT_END_DB:
        problems.append("de stem eindigt abrupt (niveau laatste 100 ms: %.0f dB), mogelijk afgekapt door de TTS" % e)
    return (not problems), problems, metrics


def verify_task_video(task_id, video_path):
    """Voor de pijplijn: zoekt de stem van de taak en controleert de eindvideo."""
    if os.environ.get("TAIL_CHECK", "on").strip().lower() == "off":
        return True, ["tail check staat uit"], {}
    voice = os.path.join(BASE_DIR, "storage", "tasks", str(task_id), "audio.mp3")
    if not os.path.exists(voice) or not os.path.exists(video_path):
        return True, ["geen stem of video gevonden, controle overgeslagen"], {}
    return check_tail(voice, video_path)


def _fmt(x, unit="s"):
    return "n/a" if x is None else ("%.2f%s" % (x, unit))


def main():
    ap = argparse.ArgumentParser(description="Toon per recente video of het einde klopt (alleen lezen).")
    ap.add_argument("--last", type=int, default=6, help="aantal recente taken (standaard 6)")
    args = ap.parse_args()
    dirs = sorted(glob.glob(os.path.join(BASE_DIR, "storage", "tasks", "*")), key=os.path.getmtime, reverse=True)
    shown = 0
    for d in dirs:
        finals = sorted(glob.glob(os.path.join(d, "final-*.mp4")))
        voice = os.path.join(d, "audio.mp3")
        if not finals or not os.path.exists(voice):
            continue
        words = None
        try:
            import json
            with open(os.path.join(d, "script.json"), encoding="utf-8") as f:
                words = len((json.load(f).get("script") or "").split())
        except Exception:
            pass
        # Alle eindbestanden van de taak: final-1.mp4 (voor de outro) en final-1_bumpered.mp4 (wat wordt geüpload).
        for fin in finals:
            ok, problems, m = check_tail(voice, fin)
            print("%s  %s  %-22s T=%s A=%s V=%s eind=%s woorden=%s" % (
                "OK  " if ok else "FOUT", os.path.basename(d)[:8], os.path.basename(fin)[:22], _fmt(m["T"]),
                _fmt(m["A"]), _fmt(m["V"]), _fmt(m["E"], "dB"), words if words is not None else "n/a"))
            for pr in problems:
                print("       - " + pr)
        shown += 1
        if shown >= args.last:
            break
    if not shown:
        print("Geen recente taakmappen met final-*.mp4 en audio.mp3 gevonden onder storage/tasks/.")


if __name__ == "__main__":
    sys.exit(main())
