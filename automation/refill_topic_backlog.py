"""
Keeps automation/topic_backlog.json from ever running dry.

daily_pipeline.py only pops topics off the backlog -- nothing ever added new
ones back, so it was on track to run empty. This tops it back up once it
drops below THRESHOLD, using the same Gemini call daily_pipeline.py already
uses for scripts/captions, informed by:
  - automation/learnings.md (which real topics/hooks/categories perform best,
    written by analyze_performance.py)
  - automation/content_inspiration.md (structural formats worth stealing,
    logged by hand during engagement rounds -- its own "How to apply" section
    already said to use it here, nothing did until now)
  - existing/already-posted topics, so it doesn't regenerate the same idea

No-ops (prints and exits 0) when the backlog is already healthy, so it's
safe to run on a daily schedule without burning LLM quota every day.

Run manually: uv run python automation/refill_topic_backlog.py
"""
import fcntl
import json
import os
import re
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from app.services import llm  # noqa: E402

BACKLOG_FILE = os.path.join(BASE_DIR, "automation", "topic_backlog.json")
LOCK_FILE = BACKLOG_FILE + ".lock"
POST_LOG_FILE = os.path.join(BASE_DIR, "automation", "post_log.json")
LEARNINGS_FILE = os.path.join(BASE_DIR, "automation", "learnings.md")
INSPIRATION_FILE = os.path.join(BASE_DIR, "automation", "content_inspiration.md")

THRESHOLD = 10  # refill once the backlog drops to this many topics or fewer
TARGET = 24  # top up to this many (at 2/day, ~12 days of runway)


def _read_text(path: str) -> str:
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as f:
        return f.read()


def _already_used_subjects() -> list[str]:
    if not os.path.exists(POST_LOG_FILE):
        return []
    with open(POST_LOG_FILE, encoding="utf-8") as f:
        entries = json.load(f)
    return [e["subject"] for e in entries if e.get("subject")]


def _build_prompt(backlog: list[str], used: list[str], amount: int) -> str:
    learnings = _read_text(LEARNINGS_FILE) or "(no learnings yet)"
    inspiration = _read_text(INSPIRATION_FILE) or "(no inspiration notes yet)"
    avoid = "\n".join(f"- {t}" for t in (backlog + used)[-80:])
    return f"""You write one-line video topics for a short-form psychology/brain-science
facts channel (YouTube Shorts/TikTok/Instagram Reels/Facebook).

Each topic is a single sentence stating an intriguing, specific psychological
or neuroscience mechanism -- the kind of fact that makes someone stop
scrolling. No numbering, no hashtags, no emoji, no quotation marks around it.
Match the style of these existing topics exactly:

- How shopping apps use 'micro-commitments' to trick your brain into making purchases you never planned on.
- Why you automatically mimic strangers' posture - the hidden mirror-neuron hijack.
- How the Zeigarnik effect turns unfinished tasks into mental ghosts that won't quit.

Here is what has actually performed well/poorly on this channel so far --
favor similar hooks, subjects and categories, avoid repeating the weak ones:

{learnings}

Here are structural formats/angles worth adapting (not to copy verbatim):

{inspiration}

Do NOT repeat or closely rephrase any of these already-used-or-queued topics:

{avoid}

Write exactly {amount} new topics, one per line, nothing else (no numbering,
no headers, no blank lines between them).
"""


def _parse_topics(response: str, amount: int) -> list[str]:
    lines = []
    for line in response.splitlines():
        line = line.strip()
        line = re.sub(r"^[\-\*\d\.\)]+\s*", "", line)  # strip bullets/numbering
        line = line.strip('"').strip()
        if line:
            lines.append(line)
    return lines[:amount]


def main():
    lock_fd = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        backlog = json.loads(_read_text(BACKLOG_FILE) or "[]")

        if len(backlog) > THRESHOLD:
            print(f"OK: {len(backlog)} topics left, above threshold ({THRESHOLD}). No refill needed.")
            return 0

        amount = TARGET - len(backlog)
        print(f"Backlog at {len(backlog)} (<= {THRESHOLD}), generating {amount} new topics...")

        used = _already_used_subjects()
        prompt = _build_prompt(backlog, used, amount)
        response = llm._generate_response(prompt=prompt)
        new_topics = _parse_topics(response, amount)

        existing_lower = {t.lower() for t in backlog + used}
        added = []
        for topic in new_topics:
            if topic.lower() not in existing_lower:
                backlog.append(topic)
                existing_lower.add(topic.lower())
                added.append(topic)

        tmp_path = BACKLOG_FILE + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(backlog, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, BACKLOG_FILE)

        print(f"Added {len(added)} new topics, backlog now at {len(backlog)}.")
        for t in added:
            print(f"  + {t}")
        return 0 if added else 1
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()


if __name__ == "__main__":
    raise SystemExit(main())
