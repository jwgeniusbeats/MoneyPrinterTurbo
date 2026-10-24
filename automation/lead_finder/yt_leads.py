"""Find recent, small YouTube creators who post long videos but (almost) no Shorts: prospects for the clip service.

Why this instead of Reddit: nobody has asked these people anything yet. Every row is checked against the
channel's real upload history, so "posted last week" is a fact, not a search-engine guess.

Needs a free YouTube Data API v3 key (Google Cloud console). Quota: each search costs 100 of the 10,000 daily units.

    export YT_API_KEY=...
    python3 automation/lead_finder/yt_leads.py                      # default niches, English
    python3 automation/lead_finder/yt_leads.py --lang nl --days 14  # Dutch-language niches
    python3 automation/lead_finder/yt_leads.py --lang all           # English and Dutch niches, any language
    python3 automation/lead_finder/yt_leads.py --queries my_queries.txt --top 30

Output: leads.csv and leads.md next to this file (not committed). Contact details are only what the channel
itself publishes in its public description. Check the rules for unsolicited messages in the creator's country
before you write to anyone (in the Netherlands, one-to-one cold messages to sole traders are restricted).
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
API = "https://www.googleapis.com/youtube/v3/"
DEFAULT_QUERIES = {
    "en": ["business coach interview", "mindset coach podcast", "entrepreneur interview podcast", "marketing podcast episode", "real estate podcast episode",
           "leadership podcast interview", "founder story interview", "fitness coach podcast", "financial advisor podcast", "health coach interview",
           "career coach podcast", "webinar replay coach", "consultant interview episode", "relationship coach podcast", "sales podcast interview"],
    "nl": ["ondernemers podcast", "coach podcast aflevering", "interview ondernemer", "podcast gesprek ondernemer", "mindset coach interview", "vastgoed podcast"],
}
EXCLUDE = re.compile(r"music|muziek|beats?\b|sounds?\b|relax|sleep|lofi|church|kerk|ministry|college|school|university|news|nieuws|highlights|vods?\b|gaming|gameplay|sermon|prayer|astrology|horoscope", re.I)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
URL = re.compile(r"https?://[^\s)>\"]+")


def http_api(path, params, key):
    q = urllib.parse.urlencode({**params, "key": key})
    with urllib.request.urlopen(urllib.request.Request(API + path + "?" + q, headers={"User-Agent": "lead-finder/1.0"}), timeout=30) as r:
        return json.loads(r.read().decode())


def iso_seconds(d):
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", d or "")
    if not m:
        return 0
    dd, h, mi, s = (int(x or 0) for x in m.groups())
    return dd * 86400 + h * 3600 + mi * 60 + s


def chunks(xs, n):
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


def find_candidates(api, queries, since, lang):
    seen = {}
    for q in queries:
        token = None
        for _page in range(2):
            j = api("search", {"part": "snippet", "q": q, "type": "video", "videoDuration": "long", "order": "relevance", "publishedAfter": since,
                               "maxResults": 50, **({"relevanceLanguage": lang} if lang else {}), **({"pageToken": token} if token else {})})
            for it in j.get("items", []):
                seen.setdefault(it["snippet"]["channelId"], q)
            token = j.get("nextPageToken")
            if not token:
                break
    return seen


def channel_info(api, ids):
    out = {}
    for batch in chunks(list(ids), 50):
        j = api("channels", {"part": "snippet,statistics,contentDetails", "id": ",".join(batch), "maxResults": 50})
        for c in j.get("items", []):
            st = c.get("statistics", {})
            out[c["id"]] = {"title": c["snippet"]["title"], "desc": c["snippet"].get("description", ""), "country": c["snippet"].get("country", ""),
                            "subs": None if st.get("hiddenSubscriberCount") else int(st.get("subscriberCount", 0)),
                            "uploads": c["contentDetails"]["relatedPlaylists"]["uploads"], "handle": c["snippet"].get("customUrl", "")}
    return out


def recent_videos(api, uploads_id, n=30):
    j = api("playlistItems", {"part": "contentDetails", "playlistId": uploads_id, "maxResults": n})
    ids = [i["contentDetails"]["videoId"] for i in j.get("items", [])]
    vids = []
    for batch in chunks(ids, 50):
        v = api("videos", {"part": "snippet,contentDetails", "id": ",".join(batch), "maxResults": 50})
        for x in v.get("items", []):
            dur = x.get("contentDetails", {}).get("duration")
            if not dur or x.get("snippet", {}).get("liveBroadcastContent") in ("live", "upcoming"):
                continue  # live streams and premieres have no final length yet
            vids.append({"id": x["id"], "title": x["snippet"]["title"], "published": x["snippet"]["publishedAt"], "sec": iso_seconds(dur)})
    vids.sort(key=lambda x: x["published"], reverse=True)
    return vids


def evaluate(info, vids, now, days, min_subs, max_subs, max_monthly=12, need_contact=True):
    """Return a lead dict or None. All facts come from the channel's own upload list."""
    if not vids or info["subs"] is None or not (min_subs <= info["subs"] <= max_subs) or EXCLUDE.search(info["title"]):
        return None
    last = datetime.fromisoformat(vids[0]["published"].replace("Z", "+00:00"))
    if now - last > timedelta(days=days):
        return None
    shorts = [v for v in vids if v["sec"] <= 61]
    longs = [v for v in vids if v["sec"] >= 600]
    if len(longs) < 3 or len(shorts) / len(vids) > 0.15:
        return None
    if sum(1 for v in longs if v["sec"] > 10800) / len(longs) > 0.3:   # mostly 3h+ = streams, loops, rebroadcasts
        return None
    month = [v for v in vids if now - datetime.fromisoformat(v["published"].replace("Z", "+00:00")) <= timedelta(days=30)]
    if len(month) > max_monthly:                                       # 13+ uploads a month = news desk, aggregator or bot
        return None
    last_long = next(v for v in vids if v["sec"] >= 600)
    emails = sorted(set(EMAIL.findall(info["desc"])))[:2]
    urls = [u for u in URL.findall(info["desc"]) if "youtube.com" not in u and "youtu.be" not in u][:2]
    if need_contact and not (emails or urls):
        return None
    score = (10 - min(10, (now - last).days)) + (1 - len(shorts) / len(vids)) * 10 + (3 if 1000 <= info["subs"] <= 30000 else 0) + (3 if emails else 0) + (2 if urls else 0) + (2 if 2 <= len(month) <= 6 else 0)
    return {"channel": info["title"], "url": f"https://www.youtube.com/{info['handle']}" if info["handle"] else "", "subs": info["subs"], "country": info["country"],
            "days_since_last_upload": (now - last).days, "uploads_last_30d": len(month), "videos_checked": len(vids), "shorts_in_those": len(shorts),
            "last_long_video": last_long["title"], "last_long_url": "https://www.youtube.com/watch?v=" + last_long["id"], "last_long_minutes": round(last_long["sec"] / 60),
            "public_email": ", ".join(emails), "website": ", ".join(urls), "score": round(score, 1)}


def run(api, queries, days=14, top=30, lang="en", min_subs=500, max_subs=100000, now=None, max_monthly=12, need_contact=True):
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cands = find_candidates(api, queries, since, lang)
    print(f"{len(cands)} channels found, checking their uploads...", file=sys.stderr, flush=True)
    infos = channel_info(api, cands)
    todo = [(cid, i) for cid, i in infos.items() if i["subs"] is not None and min_subs <= i["subs"] <= max_subs and not EXCLUDE.search(i["title"])]
    print(f"{len(todo)} left after size and name filter.", file=sys.stderr, flush=True)
    leads, done = [], [0]

    def check(item):
        cid, info = item
        try:
            lead = evaluate(info, recent_videos(api, info["uploads"]), now, days, min_subs, max_subs, max_monthly, need_contact)
        except Exception as e:  # one odd channel must never stop the whole run
            print("skipped", info.get("title"), type(e).__name__, str(e)[:80], file=sys.stderr)
            lead = None
        done[0] += 1
        if done[0] % 25 == 0:
            print(f"  {done[0]}/{len(todo)} checked", file=sys.stderr, flush=True)
        if lead:
            lead["found_via"] = cands[cid]
        return lead
    with ThreadPoolExecutor(max_workers=8) as ex:
        leads = [l for l in ex.map(check, todo) if l]
    leads.sort(key=lambda x: x["score"], reverse=True)
    return leads[:top], len(cands)


def write(leads):
    if not leads:
        return
    with open(os.path.join(HERE, "leads.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(leads[0].keys())); w.writeheader(); w.writerows(leads)
    with open(os.path.join(HERE, "leads.md"), "w") as f:
        for i, l in enumerate(leads, 1):
            f.write(f"{i}. **{l['channel']}** ({l['subs']:,} subs) {l['url']}\n   last long video ({l['last_long_minutes']} min, {l['days_since_last_upload']} days ago): {l['last_long_url']}\n   {l['uploads_last_30d']} uploads in 30 days, shorts: {l['shorts_in_those']} of {l['videos_checked']}\n   contact (public): {l['public_email'] or l['website'] or 'none in description'}\n\n")


def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--lang", default="en"); ap.add_argument("--days", type=int, default=14); ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--queries"); ap.add_argument("--min-subs", type=int, default=500); ap.add_argument("--max-subs", type=int, default=100000)
    ap.add_argument("--max-monthly", type=int, default=12, help="skip channels with more uploads than this in 30 days (news, bots)"); ap.add_argument("--any-contact", action="store_true", help="also keep channels without a public email or website")
    a = ap.parse_args(argv)
    key = os.environ.get("YT_API_KEY")
    if not key:
        print("Set YT_API_KEY first (see the docstring)."); return 2
    if a.queries:
        queries = [l.strip() for l in open(a.queries) if l.strip()]
    elif a.lang == "all":
        queries = DEFAULT_QUERIES["en"] + DEFAULT_QUERIES["nl"]
    else:
        queries = DEFAULT_QUERIES.get(a.lang, DEFAULT_QUERIES["en"])
    leads, n = run(lambda p, q: http_api(p, q, key), queries, a.days, a.top, None if a.lang == "all" else a.lang, a.min_subs, a.max_subs, None, a.max_monthly, not a.any_contact)
    write(leads); print(f"{n} channels checked, {len(leads)} fit. Files: {HERE}/leads.csv and leads.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
