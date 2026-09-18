"""One-off analysis: classify resource/songs/*.mp3 by tempo + energy so bgm
can be picked to match the narrator voice's energy instead of random.choice()
across all 29 tracks regardless of mood. Not part of the daily pipeline --
run once, hardcode the resulting BGM_BY_VOICE mapping in daily_pipeline.py.
"""

import glob
import os

import librosa
import numpy as np

SONGS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "resource", "songs")


def analyze(path: str) -> dict:
    y, sr = librosa.load(path, sr=22050, mono=True, duration=30)
    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    tempo = np.asarray(tempo).flatten()[0]
    rms = float(np.mean(librosa.feature.rms(y=y)))
    centroid = float(np.mean(librosa.feature.spectral_centroid(y=y, sr=sr)))
    return {"tempo": float(tempo), "rms": rms, "centroid": centroid}


results = []
for path in sorted(glob.glob(os.path.join(SONGS_DIR, "*.mp3"))):
    name = os.path.basename(path)
    try:
        feats = analyze(path)
        results.append((name, feats))
        print(f"{name}: tempo={feats['tempo']:.0f} bpm, rms={feats['rms']:.4f}, centroid={feats['centroid']:.0f} Hz")
    except Exception as e:
        print(f"{name}: FAILED ({e})")

# Rank by tempo for a quick energetic/calm split.
print("\n--- sorted by tempo ---")
for name, feats in sorted(results, key=lambda r: r[1]["tempo"]):
    print(f"{feats['tempo']:.0f} bpm  {name}")
