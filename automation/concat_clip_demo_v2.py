"""v2: real clipper style -- one bold word on screen at a time, synced to
speech (the "MrBeast/Hormozi" caption look), instead of whole sentences.
Built on top of concat_clip_demo.py's working trim+crop base.
"""
import json
import subprocess
import sys
import time

CONCAT_CLI = "/Users/geniusbeats/Concat/src/target/release/concat-cli"


def call(proc, method, params=None, req_id=1):
    req = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}}
    proc.stdin.write(json.dumps(req) + "\n")
    proc.stdin.flush()
    while True:
        line = proc.stdout.readline()
        if not line:
            raise RuntimeError("concat-cli api closed stdout unexpectedly")
        msg = json.loads(line)
        if msg.get("id") == req_id:
            return msg
        print("  event:", json.dumps(msg)[:150])


def word_style(content):
    return {
        "content": content, "fontFamily": "Helvetica", "fontSize": 0.065,
        "fontWeight": 900, "color": "#ffffff", "align": "center",
        "opacity": 1.0, "strokeWidth": 0.01, "strokeColor": "#000000",
        "shadow": True, "background": "", "maxWidth": 0.85,
    }


def main(source_video, words_json, project_dir, output_path, clip_start, clip_end, hook_text):
    with open(words_json) as f:
        words = json.load(f)  # [[start, end, text], ...] absolute seconds

    duration = clip_end - clip_start
    proc = subprocess.Popen(
        [CONCAT_CLI, "api"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1,
    )
    try:
        print("1. project.create")
        resp = call(proc, "project.create", {
            "location": project_dir, "name": "clip_demo_v2",
            "video": {"width": 1080, "height": 1920, "rateNum": 30, "rateDen": 1},
        }, req_id=1)
        if "error" in resp:
            raise RuntimeError(resp)
        path = f"{project_dir}/clip_demo_v2"

        print("2. media.import")
        resp = call(proc, "media.import", {"path": path, "file": source_video}, req_id=2)
        media_id = resp["result"]["createdId"]

        print("3. add + trim + reposition + crop")
        resp = call(proc, "edit.apply", {"path": path, "command":
            {"op": "addClipAtFirstFree", "mediaId": media_id, "start": 0.0}}, req_id=3)
        clip_id = resp["result"]["createdId"]

        call(proc, "edit.apply", {"path": path, "command":
            {"op": "trimClip", "clipId": clip_id, "edge": "start", "delta": clip_start, "ripple": False}}, req_id=4)
        call(proc, "edit.apply", {"path": path, "command":
            {"op": "moveClips", "moves": [{"clipId": clip_id, "start": 0.0, "trackId": "T1"}]}}, req_id=5)
        doc = call(proc, "project.document", {"path": path}, req_id=6)
        remaining = next(c["duration"] for c in doc["result"]["timelines"][0]["clips"] if c["id"] == clip_id)
        call(proc, "edit.apply", {"path": path, "command":
            {"op": "trimClip", "clipId": clip_id, "edge": "end", "delta": duration - remaining, "ripple": False}}, req_id=7)
        call(proc, "edit.apply", {"path": path, "command":
            {"op": "setClipTransform", "clipId": clip_id, "scale": 3.16}}, req_id=8)

        print("4. hook text (0-1.5s)")
        call(proc, "edit.apply", {"path": path, "command": {
            "op": "addTextClip", "above": True, "start": 0.0, "duration": 1.5,
            "style": word_style(hook_text),
        }}, req_id=9)

        print(f"5. batch: {len(words)} word-by-word captions")
        commands = []
        for i, (w_start, w_end, text) in enumerate(words):
            rel_start = max(0.0, w_start - clip_start)
            if rel_start >= duration:
                continue
            next_start = words[i + 1][0] - clip_start if i + 1 < len(words) else (w_end - clip_start)
            word_dur = max(0.15, min(next_start - rel_start, duration - rel_start))
            commands.append({
                "op": "addTextClip", "above": True, "start": rel_start, "duration": word_dur,
                "offsetY": 0.28, "style": word_style(text),
            })
        resp = call(proc, "edit.apply", {"path": path, "command": {"op": "batch", "commands": commands}}, req_id=10)
        if "error" in resp:
            raise RuntimeError(resp)
        print(f"  placed {len(commands)} word clips")

        print("6. export.run")
        resp = call(proc, "export.run", {"path": path, "output": output_path}, req_id=11)
        print(json.dumps(resp)[:300])
        if "error" in resp:
            raise RuntimeError(resp)

        print("7. waiting for export.done ...")
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
    main(
        source_video=sys.argv[1],
        words_json=sys.argv[2],
        project_dir=sys.argv[3],
        output_path=sys.argv[4],
        clip_start=65.0,
        clip_end=90.0,
        hook_text="Can I get your number?",
    )
