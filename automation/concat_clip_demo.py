"""Clip-service proof of concept: take a long source video + a Whisper
transcript segment, cut a highlight window, crop to 9:16, and burn in a
caption for that window -- the actual "repurpose long content into a
short" workflow discussed 2026-09-19, not just a bare text overlay.
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
        print("  event:", json.dumps(msg)[:200])


def main(source_video, project_dir, output_path, clip_start, clip_end, hook_text, caption_text, caption_rel_start, caption_rel_end):
    duration = clip_end - clip_start
    proc = subprocess.Popen(
        [CONCAT_CLI, "api"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1,
    )
    try:
        print("1. project.create (portrait 1080x1920)")
        resp = call(proc, "project.create", {
            "location": project_dir, "name": "clip_demo",
            "video": {"width": 1080, "height": 1920, "rateNum": 30, "rateDen": 1},
        }, req_id=1)
        if "error" in resp:
            raise RuntimeError(resp)
        path = f"{project_dir}/clip_demo"

        print("2. media.import")
        resp = call(proc, "media.import", {"path": path, "file": source_video}, req_id=2)
        if "error" in resp:
            raise RuntimeError(resp)
        media_id = resp["result"]["createdId"]

        print("3. edit.apply -> place full source clip")
        resp = call(proc, "edit.apply", {
            "path": path,
            "command": {"op": "addClipAtFirstFree", "mediaId": media_id, "start": 0.0},
        }, req_id=3)
        if "error" in resp:
            raise RuntimeError(resp)
        clip_id = resp["result"]["createdId"]
        print("  clip_id:", clip_id)

        print(f"4. trim head to {clip_start}s")
        resp = call(proc, "edit.apply", {
            "path": path,
            "command": {"op": "trimClip", "clipId": clip_id, "edge": "start", "delta": clip_start, "ripple": False},
        }, req_id=4)
        if "error" in resp:
            raise RuntimeError(resp)

        print("4b. move clip back to timeline 0 (head trim also shifted `start`)")
        resp = call(proc, "edit.apply", {
            "path": path,
            "command": {"op": "moveClips", "moves": [{"clipId": clip_id, "start": 0.0, "trackId": "T1"}]},
        }, req_id=42)
        if "error" in resp:
            raise RuntimeError(resp)

        print(f"5. trim tail to {duration}s total")
        remaining = None
        doc = call(proc, "project.document", {"path": path}, req_id=41)
        for clip in doc["result"]["timelines"][0]["clips"]:
            if clip["id"] == clip_id:
                remaining = clip["duration"]
        tail_delta = duration - remaining  # negative shortens
        resp = call(proc, "edit.apply", {
            "path": path,
            "command": {"op": "trimClip", "clipId": clip_id, "edge": "end", "delta": tail_delta, "ripple": False},
        }, req_id=5)
        if "error" in resp:
            raise RuntimeError(resp)

        print("6. cover-crop to vertical (scale up landscape source)")
        resp = call(proc, "edit.apply", {
            "path": path,
            "command": {"op": "setClipTransform", "clipId": clip_id, "scale": 1.78},
        }, req_id=6)
        print("  ", json.dumps(resp)[:200])

        print("7. hook text (first 2s)")
        resp = call(proc, "edit.apply", {
            "path": path,
            "command": {
                "op": "addTextClip", "above": True, "start": 0.0, "duration": 2.0,
                "style": {
                    "content": hook_text, "fontFamily": "Helvetica", "fontSize": 0.07,
                    "fontWeight": 800, "color": "#ffffff", "align": "center",
                    "opacity": 1.0, "strokeWidth": 0.012, "strokeColor": "#000000",
                    "shadow": True, "background": "",
                },
            },
        }, req_id=7)
        if "error" in resp:
            raise RuntimeError(resp)

        print("8. caption over the highlight line")
        resp = call(proc, "edit.apply", {
            "path": path,
            "command": {
                "op": "addTextClip", "above": True, "start": caption_rel_start,
                "duration": caption_rel_end - caption_rel_start, "offsetY": 0.32,
                "style": {
                    "content": caption_text, "fontFamily": "Helvetica", "fontSize": 0.055,
                    "fontWeight": 700, "color": "#ffffff", "align": "center",
                    "opacity": 1.0, "strokeWidth": 0.01, "strokeColor": "#000000",
                    "shadow": True, "background": "",
                },
            },
        }, req_id=8)
        if "error" in resp:
            raise RuntimeError(resp)

        print("9. export.run")
        resp = call(proc, "export.run", {"path": path, "output": output_path}, req_id=9)
        print(json.dumps(resp)[:300])
        if "error" in resp:
            raise RuntimeError(resp)

        print("10. waiting for export.done ...")
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
        project_dir=sys.argv[2],
        output_path=sys.argv[3],
        clip_start=65.0,
        clip_end=90.0,
        hook_text="Can I get your number?",
        caption_text="Can I get your number please\nwhy I can't get your number",
        caption_rel_start=5.9,
        caption_rel_end=13.1,
    )
