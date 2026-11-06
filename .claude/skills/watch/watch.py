#!/usr/bin/env python3
"""Haal een video-link binnen en lever transcript + frames + caption.

Gebruik: python3 watch.py <url> <outdir>
Instagram: via de embed-pagina (yt-dlp krijgt vaak 429). Overige sites: yt-dlp.
Output in <outdir>: reel.mp4, caption.txt, transcript.txt, frames.jpg (contactsheet).
"""
import html
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

UA = "Mozilla/5.0"


def http(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60)


def fetch_instagram(url, out):
    m = re.search(r"instagram\.com/(?:[\w.]+/)?(reel|reels|p|tv)/([\w-]+)", url)
    if not m:
        raise RuntimeError("geen geldige Instagram-link")
    page = http(f"https://www.instagram.com/{m.group(1).rstrip('s')}/{m.group(2)}/embed/captioned/").read().decode("utf8", "replace")
    v = re.search(r'video_url\\":\\"(.*?)\\"', page)
    if not v:
        raise RuntimeError("geen video_url in embed (privé, geblokkeerd, of fotopost)")
    video = json.loads('"' + v.group(1) + '"').replace("\\/", "/")
    c = re.search(r'class="Caption">(.*?)<div class="CaptionComments"', page, re.S)
    caption = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", c.group(1)))).strip() if c else ""
    (out / "reel.mp4").write_bytes(http(video).read())
    (out / "caption.txt").write_text(caption)


def fetch_other(url, out):
    subprocess.run(["yt-dlp", "-q", "--no-warnings", "-o", str(out / "reel.%(ext)s"), "-f", "mp4/best", url], check=True)
    vid = next(out.glob("reel.*"))
    if vid.name != "reel.mp4":
        vid.rename(out / "reel.mp4")


def transcribe(video, out):
    import numpy as np
    from faster_whisper import WhisperModel

    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-f", "f32le", "-ac", "1", "-ar", "16000", "-"],
                         capture_output=True, check=True).stdout
    if not raw:
        return "(geen audiospoor)"
    segs, info = WhisperModel("small", compute_type="int8").transcribe(np.frombuffer(raw, dtype=np.float32))
    return f"[{info.language}]\n" + "\n".join(f"[{s.start:.0f}s] {s.text.strip()}" for s in segs)


def frames(video, out):
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)],
                               capture_output=True, text=True, check=True).stdout)
    step = max(2, round(dur / 9))
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-vf", f"fps=1/{step},scale=480:-1,tile=3x3",
                    "-frames:v", "1", str(out / "frames.jpg")], check=True)
    return dur


def main():
    url, out = sys.argv[1], Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    (fetch_instagram if "instagram.com" in url else fetch_other)(url, out)
    video = out / "reel.mp4"
    dur = frames(video, out)
    (out / "transcript.txt").write_text(transcribe(video, out))
    print(f"klaar: {dur:.0f}s video in {out}")
    for f in ("caption.txt", "transcript.txt", "frames.jpg"):
        print(" -", out / f)


if __name__ == "__main__":
    main()
