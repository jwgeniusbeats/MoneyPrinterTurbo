"""Proof-of-concept: drive Concat's JSON-RPC `api` mode end to end (project
create -> import -> add a text overlay -> export) to prove the scripted
edit path works, not just the bare probe/render primitives.

Concat: https://github.com/jub0t/Concat -- local build at ~/Concat/src/target/release/concat-cli
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
        # else: an event notification (e.g. export.progress) -- print and keep reading
        print("  event:", json.dumps(msg)[:200])


def main(source_video: str, project_dir: str, output_path: str):
    proc = subprocess.Popen(
        [CONCAT_CLI, "api"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1,
    )
    try:
        print("1. project.create")
        resp = call(proc, "project.create", {
            "location": project_dir, "name": "nognize_demo",
            "video": {"width": 1080, "height": 1920, "rateNum": 30, "rateDen": 1},
        }, req_id=1)
        print(json.dumps(resp)[:300])
        if "error" in resp:
            raise RuntimeError(resp["error"])
        path = f"{project_dir}/nognize_demo"

        print("2. media.import")
        resp = call(proc, "media.import", {"path": path, "file": source_video}, req_id=2)
        print(json.dumps(resp)[:300])
        media_id = resp["result"]["createdId"]

        print("3. edit.apply -> AddClipAtFirstFree (place the source video)")
        resp = call(proc, "edit.apply", {
            "path": path,
            "command": {"op": "addClipAtFirstFree", "mediaId": media_id, "start": 0.0},
        }, req_id=3)
        print(json.dumps(resp)[:300])

        print("4. edit.apply -> AddTextClip (caption overlay)")
        resp = call(proc, "edit.apply", {
            "path": path,
            "command": {
                "op": "addTextClip",
                "above": True,
                "start": 0.5,
                "duration": 3.0,
                "style": {
                    "content": "Made with Concat",
                    "fontFamily": "Helvetica",
                    "fontSize": 0.08,
                    "fontWeight": 700,
                    "italic": False,
                    "color": "#ffffff",
                    "align": "center",
                    "opacity": 1.0,
                    "strokeWidth": 0.01,
                    "strokeColor": "#000000",
                    "shadow": True,
                    "background": "",
                },
            },
        }, req_id=4)
        print(json.dumps(resp)[:300])

        print("5. export.run")
        resp = call(proc, "export.run", {"path": path, "output": output_path}, req_id=5)
        print(json.dumps(resp)[:300])

        # Drain events until export.done / export.failed
        print("6. waiting for export.done ...")
        deadline = time.time() + 120
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            msg = json.loads(line)
            print("  event:", json.dumps(msg)[:300])
            method = msg.get("method", "")
            if method in ("export.done", "export.failed"):
                print("RESULT:", method)
                break
    finally:
        proc.terminate()
        stderr = proc.stderr.read()
        if stderr:
            print("--- stderr ---", stderr[-1000:], file=sys.stderr)


if __name__ == "__main__":
    main(
        source_video=sys.argv[1],
        project_dir=sys.argv[2],
        output_path=sys.argv[3],
    )
