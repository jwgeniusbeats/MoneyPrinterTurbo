"""One-off crop fix for webinar_example: the clip opens on ~17s of a
wide/moving shot (speaker walking to the podium while an off-camera host
reads his bio) before settling into the tight solo crop. At the default
scale (3.16) this wide window mostly shows empty curtain. Zooms out to
show the full podium during that window, then back to the tight crop once
he's actually at the mic and talking.
"""
import json
import subprocess
import sys
import time

CONCAT_CLI = "/Users/geniusbeats/Concat/src/target/release/concat-cli"
PROJECT_PATH = "automation/portfolio_projects/webinar_example"
OUTPUT = "automation/portfolio_output/webinar_example.mp4"
CLIP_DURATION = 200.3 - 163.7  # 36.6s

BASE_SCALE, BASE_OFFSET_X = 3.16, 0.0
WIDE_SCALE = 1.47


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
        commands += keys_for(clip_id, 0.0, WIDE_SCALE, BASE_OFFSET_X)
        commands += keys_for(clip_id, 16.5, WIDE_SCALE, BASE_OFFSET_X)
        commands += keys_for(clip_id, 17.3, BASE_SCALE, BASE_OFFSET_X)

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
