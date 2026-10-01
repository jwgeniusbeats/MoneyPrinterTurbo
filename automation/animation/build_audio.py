#!/usr/bin/env python3
"""Voice-over + music for an explainer script.

  python automation/animation/build_audio.py script.json --out-dir build/ \
      [--voice af_heart] [--base-url http://127.0.0.1:8880/v1] \
      [--voice-dir customer_recordings/] [--music track.mp3 | --music generate]

For every scene's `say` text it gets a voice clip (OpenAI-compatible
/audio/speech server such as the local Kokoro on :8880 or a Chatterbox server
for cloned voices, OR the customer's own recordings via --voice-dir), sets the
scene length from the real audio length (so no hand-timing), builds one voice
track, optionally mixes music underneath with ducking (music gets quieter when
the voice speaks) and writes:

  <out-dir>/<name>.timed.json   script with dur/lead/speech filled in
  <out-dir>/mix.wav             voice (+ music)

Then:  node automation/animation/render.mjs <out-dir>/<name>.timed.json out.mp4 --audio <out-dir>/mix.wav

Needs ffmpeg on PATH (or FFMPEG env var) and `requests`.
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import wave

import requests

FFMPEG = os.environ.get("FFMPEG", "ffmpeg")
LEAD, TAIL, MIN_DUR = 0.25, 0.45, 3.0  # silence before/after speech in a scene, minimum scene length


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        sys.exit(f"ffmpeg failed:\n{' '.join(cmd)}\n{p.stderr[-1500:]}")


def to_wav(src, dst):
    run([FFMPEG, "-y", "-loglevel", "error", "-i", src, "-ar", "24000", "-ac", "1", dst])


def wav_seconds(path):
    with wave.open(path) as w:
        return w.getnframes() / w.getframerate()


def tts_http(base_url, api_key, model, voice, speed, text, dst_wav):
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    r = requests.post(
        base_url.rstrip("/") + "/audio/speech",
        headers=headers,
        json={"model": model, "voice": voice, "input": text, "speed": speed, "response_format": "mp3"},
        timeout=600,
    )
    r.raise_for_status()
    raw = dst_wav + ".raw"
    with open(raw, "wb") as f:
        f.write(r.content)
    to_wav(raw, dst_wav)
    os.remove(raw)


def generated_bed(seconds, dst):
    """Plain ambient pad (A minor chord + slow tremolo). Licence-free because it
    is synthesised here; a real track from the customer usually sounds better."""
    run([
        FFMPEG, "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", f"sine=f=110:d={seconds}",
        "-f", "lavfi", "-i", f"sine=f=220:d={seconds}",
        "-f", "lavfi", "-i", f"sine=f=261.63:d={seconds}",
        "-f", "lavfi", "-i", f"sine=f=329.63:d={seconds}",
        "-filter_complex",
        "[0][1][2][3]amix=inputs=4:normalize=0,tremolo=f=0.2:d=0.5,lowpass=f=1400,aecho=0.8:0.6:700:0.3[a]",
        "-map", "[a]", "-ar", "24000", "-ac", "1", dst,
    ])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("script")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--base-url", default="http://127.0.0.1:8880/v1")
    ap.add_argument("--api-key", default="")
    ap.add_argument("--model", default="kokoro")
    ap.add_argument("--voice", default="af_heart")
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--voice-dir", help="folder with the customer's own recordings: scene_01.*, scene_02.* ...")
    ap.add_argument("--music", help="music file, or the word 'generate'")
    ap.add_argument("--music-gain-db", type=float, default=-4.0)
    a = ap.parse_args()

    os.makedirs(a.out_dir, exist_ok=True)
    script = json.load(open(a.script))
    scenes = script["scenes"]

    wavs = []
    for i, sc in enumerate(scenes, 1):
        wav = os.path.join(a.out_dir, f"scene_{i:02d}.wav")
        if a.voice_dir:
            found = sorted(glob.glob(os.path.join(a.voice_dir, f"scene_{i:02d}.*")))
            if not found:
                sys.exit(f"missing recording for scene {i}: {a.voice_dir}/scene_{i:02d}.*")
            to_wav(found[0], wav)
        else:
            tts_http(a.base_url, a.api_key, a.model, a.voice, a.speed, sc["say"], wav)
        sc["speech"] = round(wav_seconds(wav), 3)
        sc["lead"] = LEAD
        sc["dur"] = round(max(MIN_DUR, sc["speech"] + LEAD + TAIL), 3)
        wavs.append(wav)
        print(f"scene {i}: speech {sc['speech']:.2f}s -> dur {sc['dur']:.2f}s")

    total = sum(s["dur"] for s in scenes)
    cmd = [FFMPEG, "-y", "-loglevel", "error"]
    for w in wavs:
        cmd += ["-i", w]
    parts = []
    for i, sc in enumerate(scenes):
        ms = int(LEAD * 1000)
        parts.append(f"[{i}:a]adelay={ms}|{ms},apad=whole_dur={sc['dur']}[v{i}]")
    n = len(scenes)
    parts.append("".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=0:a=1,loudnorm=I=-16:TP=-1.5:LRA=11[voice]")

    music = a.music
    if music == "generate":
        music = os.path.join(a.out_dir, "bed.wav")
        generated_bed(total + 2, music)
    if music:
        cmd += ["-stream_loop", "-1", "-i", music]
        parts.append("[voice]asplit=2[vo][vsc]")
        parts.append(f"[{n}:a]volume={a.music_gain_db}dB[m]")
        parts.append("[m][vsc]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=400[md]")
        parts.append("[vo][md]amix=inputs=2:duration=first:normalize=0[mix]")
        last = "mix"
    else:
        last = "voice"
    parts.append(f"[{last}]afade=t=out:st={max(0, total - 1.5):.2f}:d=1.5[out]")
    mix = os.path.join(a.out_dir, "mix.wav")
    cmd += ["-filter_complex", ";".join(parts), "-map", "[out]", "-t", f"{total:.3f}", "-ar", "44100", mix]
    run(cmd)

    name = os.path.splitext(os.path.basename(a.script))[0]
    timed = os.path.join(a.out_dir, f"{name}.timed.json")
    json.dump(script, open(timed, "w"), indent=2)
    print(f"wrote {timed} and {mix} ({total:.1f}s)")


if __name__ == "__main__":
    main()
