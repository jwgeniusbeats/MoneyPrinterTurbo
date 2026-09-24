"""One-off crop fix for podcast_example: right as Kenda starts answering
(clip-relative 4.7-9.7s, source 219.0-224.0s -- NOT the clip open, which
is already solo Jim and frames fine) the source cuts to a Zoom-style
split-screen, and the base full-bleed crop cuts straight down the middle.
Keyframes the crop into Kenda's own box (she's the one talking) for that
window, then back to the normal solo crop -- on top of the existing
mid-clip b-roll dip (31.7-33.7s, two field researchers).
"""
import json
import subprocess
import sys
import time

CONCAT_CLI = "/Users/geniusbeats/Concat/src/target/release/concat-cli"
PROJECT_PATH = "automation/portfolio_projects/podcast_example"
OUTPUT = "automation/portfolio_output/podcast_example.mp4"
CLIP_DURATION = 266.4 - 214.3  # 52.1s

BASE_SCALE, BASE_OFFSET_X = 3.16, 0.0
BOX_SCALE = 3.856
KENDA_OFFSET_X = 0.933


def call(proc, method, params, req_id):
    proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}) + "\n")
    proc.stdin.flush()
    while True:
        line = proc.stdout.readline()
        if not line:
            raise RuntimeError("concat-cli api closed stdout unexpectedly")
        msg = json.loads(line)
        if msg.get("id") == req_id:
            return msg
        print("  event:", json.dumps(msg)[:150])


def keys_for(clip_id, t, scale, offset_x):
    at = max(0.0, min(1.0, t / CLIP_DURATION))
    return [
        {"op": "setClipKey", "clipId": clip_id, "property": "scale", "at": at, "value": scale},
        {"op": "setClipKey", "clipId": clip_id, "property": "offsetX", "at": at, "value": offset_x},
        {"op": "setClipKey", "clipId": clip_id, "property": "offsetY", "at": at, "value": 0.0},
    ]


def main():
    proc = subprocess.Popen(
        [CONCAT_CLI, "api"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1,
    )
    try:
        resp = call(proc, "project.open", {"path": PROJECT_PATH}, 1)
        if "error" in resp:
            raise RuntimeError(resp)
        doc = call(proc, "project.document", {"path": PROJECT_PATH}, 2)
        clip_id = next(c["id"] for c in doc["result"]["timelines"][0]["clips"] if c.get("mediaId"))
        print("clip_id:", clip_id)

        commands = []
        # Base crop holds through the clip open (already solo Jim, frames fine)
        commands += keys_for(clip_id, 0.0, BASE_SCALE, BASE_OFFSET_X)
        commands += keys_for(clip_id, 4.4, BASE_SCALE, BASE_OFFSET_X)
        # Split-screen appears as Kenda starts answering -- crop into her box
        commands += keys_for(clip_id, 4.7, BOX_SCALE, KENDA_OFFSET_X)
        commands += keys_for(clip_id, 9.4, BOX_SCALE, KENDA_OFFSET_X)
        # Back to the normal solo full-bleed crop
        commands += keys_for(clip_id, 9.7, BASE_SCALE, BASE_OFFSET_X)
        # Existing mid-clip b-roll dip (two field researchers), unchanged
        commands += keys_for(clip_id, 31.4, BASE_SCALE, BASE_OFFSET_X)
        commands += keys_for(clip_id, 31.7, 1.47, 0.0)
        commands += keys_for(clip_id, 33.7, 1.47, 0.0)
        commands += keys_for(clip_id, 34.0, BASE_SCALE, BASE_OFFSET_X)

        resp = call(proc, "edit.apply", {"path": PROJECT_PATH, "command": {"op": "batch", "commands": commands}}, 3)
        if "error" in resp:
            raise RuntimeError(resp)
        print("keys applied")

        resp = call(proc, "project.save", {"path": PROJECT_PATH}, 4)
        if "error" in resp:
            raise RuntimeError(resp)

        resp = call(proc, "export.run", {"path": PROJECT_PATH, "output": OUTPUT}, 5)
        print(json.dumps(resp)[:300])
        if "error" in resp:
            raise RuntimeError(resp)

        deadline = time.time() + 180
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            msg = json.loads(line)
            method = msg.get("method", "")
            if method in ("export.done", "export.failed"):
                print("RESULT:", method, json.dumps(msg)[:300])
                break
    finally:
        proc.terminate()
        stderr = proc.stderr.read()
        if stderr:
            print("--- stderr ---", stderr[-1500:], file=sys.stderr)


if __name__ == "__main__":
    main()
