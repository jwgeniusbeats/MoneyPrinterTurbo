"""Generic clip builder for the clip service: any source video + time
range + hook text -> vertical 9:16 short with word-by-word burned-in
captions (MrBeast/Hormozi style). Auto-transcribes via faster-whisper
(reusing app.services.subtitle) unless words are passed in explicitly.

Supersedes concat_clip_demo.py / concat_clip_demo_v2.py, which were
hardcoded to one test video. Built on concat-cli's project/edit/export API.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

CONCAT_CLI = "/Users/geniusbeats/Concat/src/target/release/concat-cli"
REPO_ROOT = "/Users/geniusbeats/MoneyPrinterTurbo"
sys.path.insert(0, REPO_ROOT)  # for app.services.subtitle (faster-whisper wrapper)


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


def word_style(content, font_size=0.065):
    return {
        "content": content, "fontFamily": "Helvetica", "fontSize": font_size,
        "fontWeight": 900, "color": "#ffffff", "align": "center",
        "opacity": 1.0, "strokeWidth": 0.01, "strokeColor": "#000000",
        "shadow": True, "background": "", "maxWidth": 0.85,
    }


def extract_audio(source_video, start, end, out_path):
    subprocess.run(
        ["ffmpeg", "-y", "-ss", str(start), "-to", str(end), "-i", source_video,
         "-vn", "-ac", "1", "-ar", "16000", out_path],
        check=True, capture_output=True,
    )


def transcribe_words(source_video, clip_start, clip_end):
    """Whisper-transcribes only the [clip_start, clip_end) window, so
    returned timestamps are already relative to the clip (0 = clip_start).
    Returns [(rel_start, rel_end, text), ...]."""
    from app.services import subtitle
    with tempfile.TemporaryDirectory() as tmp:
        audio_path = os.path.join(tmp, "clip_audio.wav")
        extract_audio(source_video, clip_start, clip_end, audio_path)
        words_out = subtitle.create(
            audio_path, subtitle_file=os.path.join(tmp, "clip.srt"), word_level=True
        )
    if not words_out:
        raise RuntimeError(
            "whisper produced no words -- check audio track / model download"
        )
    return [(w["start"], w["end"], w["word"]) for w in words_out]


def slice_words_for_clip(range_words, range_start, clip_start, clip_end):
    """Slices a transcribe_words(source, range_start, range_end) result
    down to one clip's window inside that range, re-based so 0 = clip_start
    (the format build_clip's `words` argument expects).

    Use this for Standard/Premium orders (multiple clips cut from the same
    source video): transcribe the whole span once with transcribe_words(
    source, min_clip_start, max_clip_end), then call this per clip instead
    of letting each build_clip() call re-run Whisper on its own. Whisper
    model load + inference is the slow part of fulfillment, this collapses
    N runs into 1 for an N-clip order from one source.
    """
    offset = clip_start - range_start
    sliced = []
    for rel_start, rel_end, text in range_words:
        abs_start = rel_start + range_start
        if clip_start <= abs_start < clip_end:
            sliced.append((rel_start - offset, rel_end - offset, text))
    return sliced


def build_clip(source_video, clip_start, clip_end, hook_text, output_path,
                project_dir, words=None, project_name=None, hook_duration=1.5,
                scale=3.16, offset_x=0.0, offset_y=0.0, crop_keyframes=None):
    """Cuts [clip_start, clip_end) out of source_video, crops to 9:16, and
    burns in hook_text (first hook_duration seconds) plus word-by-word
    captions for the rest.

    words: optional pre-computed [(rel_start, rel_end, text), ...], timed
    relative to clip_start (0 = clip_start). If omitted, auto-transcribes
    via Whisper on just that window.

    scale: cover-crop multiplier (3.16 fills a 1080x1920 canvas from a
    1920x1080 source; see setClipTransform below). offset_x/offset_y pan
    the crop -- needed for wide multi-person shots where a dead-center crop
    lands between two speakers instead of on either of them. Both are
    fractions of the canvas; to centre the crop on a source point at
    fractional position p (0=left edge, 1=right edge), use
    offset_x = scale * (0.5 - p).

    crop_keyframes: optional list of dicts for briefly overriding the crop
    -- e.g. a b-roll cutaway to a wide multi-person shot in the middle of an
    otherwise single-speaker clip, where the static crop above would land
    between people rather than on either of them. Each dict:
    {start, end, scale, offset_x, offset_y=0.0, transition=0.3} (all
    relative to clip_start). Ramps from the base crop to the override over
    `transition` seconds, holds it for [start, end], then ramps back.
    """
    if words is None:
        words = transcribe_words(source_video, clip_start, clip_end)

    duration = clip_end - clip_start
    explicit_name = project_name is not None
    project_name = project_name or f"clip_{int(time.time())}"
    path = f"{project_dir}/{project_name}"

    output_dir = os.path.dirname(output_path)
    if output_dir:
        # export.run writes temp render files as siblings of output_path
        # and doesn't create missing directories itself (concat-export's
        # render() assumes output.parent() already exists). Needed when
        # the buyer uploaded a file directly, skipping the yt-dlp download
        # step that would otherwise have created this folder as a side
        # effect.
        os.makedirs(output_dir, exist_ok=True)

    if explicit_name and os.path.isdir(path):
        # project.create refuses to touch a folder that already holds a
        # manifest (revision workflow: re-running with the same
        # --project-name is how order_fulfillment_checklist.md says to
        # redo a clip). Clear it first so the rebuild starts clean.
        print(f"0. clearing existing project at {path} (rebuilding for revision)")
        shutil.rmtree(path)

    proc = subprocess.Popen(
        [CONCAT_CLI, "api"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1,
    )
    try:
        print("1. project.create")
        resp = call(proc, "project.create", {
            "location": project_dir, "name": project_name,
            "video": {"width": 1080, "height": 1920, "rateNum": 30, "rateDen": 1},
        }, req_id=1)
        if "error" in resp:
            raise RuntimeError(resp)

        print("2. media.import")
        resp = call(proc, "media.import", {"path": path, "file": source_video}, req_id=2)
        if "error" in resp:
            raise RuntimeError(resp)
        media_id = resp["result"]["createdId"]

        print("3. add + trim + reposition + crop")
        resp = call(proc, "edit.apply", {"path": path, "command":
            {"op": "addClipAtFirstFree", "mediaId": media_id, "start": 0.0}}, req_id=3)
        if "error" in resp:
            raise RuntimeError(resp)
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
            {"op": "setClipTransform", "clipId": clip_id, "scale": scale,
             "offsetX": offset_x, "offsetY": offset_y}}, req_id=8)

        print(f"4. hook text (0-{hook_duration}s)")
        call(proc, "edit.apply", {"path": path, "command": {
            "op": "addTextClip", "above": True, "start": 0.0, "duration": hook_duration,
            "style": word_style(hook_text),
        }}, req_id=9)

        print(f"5. batch: {len(words)} word-by-word captions")
        commands = []
        for i, (rel_start, rel_end, text) in enumerate(words):
            if rel_start >= duration:
                continue
            next_start = words[i + 1][0] if i + 1 < len(words) else rel_end
            word_dur = max(0.15, min(next_start - rel_start, duration - rel_start))
            commands.append({
                "op": "addTextClip", "above": True, "start": rel_start, "duration": word_dur,
                "offsetY": 0.28, "style": word_style(text),
            })
        resp = call(proc, "edit.apply", {"path": path, "command": {"op": "batch", "commands": commands}}, req_id=10)
        if "error" in resp:
            raise RuntimeError(resp)
        print(f"  placed {len(commands)} word clips")

        if crop_keyframes:
            print(f"5b. {len(crop_keyframes)} crop keyframe override(s)")
            key_commands = []
            next_id = 100
            for kf in crop_keyframes:
                transition = kf.get("transition", 0.3)
                kf_offset_y = kf.get("offset_y", 0.0)
                points = [
                    (kf["start"] - transition, scale, offset_x, offset_y),
                    (kf["start"], kf["scale"], kf["offset_x"], kf_offset_y),
                    (kf["end"], kf["scale"], kf["offset_x"], kf_offset_y),
                    (kf["end"] + transition, scale, offset_x, offset_y),
                ]
                for t, s, ox, oy in points:
                    at = max(0.0, min(1.0, t / duration))
                    for prop, value in (("scale", s), ("offsetX", ox), ("offsetY", oy)):
                        key_commands.append({
                            "op": "setClipKey", "clipId": clip_id, "property": prop,
                            "at": at, "value": value,
                        })
            resp = call(proc, "edit.apply", {"path": path, "command": {"op": "batch", "commands": key_commands}}, req_id=next_id)
            if "error" in resp:
                raise RuntimeError(resp)

        print("6. project.save")
        resp = call(proc, "project.save", {"path": path}, req_id=12)
        if "error" in resp:
            raise RuntimeError(resp)

        print("7. export.run")
        resp = call(proc, "export.run", {"path": path, "output": output_path}, req_id=11)
        print(json.dumps(resp)[:300])
        if "error" in resp:
            raise RuntimeError(resp)

        print("8. waiting for export.done ...")
        deadline = time.time() + 180
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            msg = json.loads(line)
            method = msg.get("method", "")
            if method in ("export.done", "export.failed"):
                print("RESULT:", method, json.dumps(msg)[:300])
                if method == "export.failed":
                    raise RuntimeError(msg)
                break
    finally:
        proc.terminate()
        stderr = proc.stderr.read()
        if stderr:
            print("--- stderr ---", stderr[-1500:], file=sys.stderr)

    return output_path


def _load_words_json(words_json_path):
    """Loads a pre-computed words file: [[rel_start, rel_end, text], ...]."""
    with open(words_json_path) as f:
        return [tuple(w) for w in json.load(f)]


def main():
    p = argparse.ArgumentParser(description="Build one vertical short from a source video + time range.")
    p.add_argument("source_video")
    p.add_argument("clip_start", type=float)
    p.add_argument("clip_end", type=float)
    p.add_argument("hook_text")
    p.add_argument("output_path")
    p.add_argument("--project-dir", default=tempfile.gettempdir())
    p.add_argument("--project-name", default=None)
    p.add_argument("--words-json", default=None, help="Optional pre-computed [[rel_start, rel_end, text], ...] instead of auto-transcribing.")
    p.add_argument("--scale", type=float, default=3.16, help="Cover-crop multiplier.")
    p.add_argument("--offset-x", type=float, default=0.0, help="Pan the crop horizontally (fraction of canvas). See build_clip docstring.")
    p.add_argument("--offset-y", type=float, default=0.0, help="Pan the crop vertically (fraction of canvas).")
    p.add_argument("--hook-duration", type=float, default=1.5, help="Seconds the hook text stays on screen.")
    p.add_argument("--crop-keyframes-json", default=None, help="Optional JSON file: list of {start, end, scale, offset_x, offset_y, transition} dicts for briefly overriding the crop (b-roll cutaways, wide shots). See build_clip docstring.")
    args = p.parse_args()

    words = _load_words_json(args.words_json) if args.words_json else None
    crop_keyframes = None
    if args.crop_keyframes_json:
        with open(args.crop_keyframes_json) as f:
            crop_keyframes = json.load(f)

    build_clip(
        source_video=args.source_video, clip_start=args.clip_start, clip_end=args.clip_end,
        hook_text=args.hook_text, output_path=args.output_path, project_dir=args.project_dir,
        words=words, project_name=args.project_name, hook_duration=args.hook_duration,
        scale=args.scale, offset_x=args.offset_x, offset_y=args.offset_y,
        crop_keyframes=crop_keyframes,
    )


if __name__ == "__main__":
    main()
