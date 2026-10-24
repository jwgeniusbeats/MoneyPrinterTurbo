"""Offline check of the lead filter with fake API answers. Run: python3 automation/lead_finder/selfcheck.py"""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from automation.lead_finder import yt_leads as y  # noqa: E402

NOW = datetime(2026, 10, 19, tzinfo=timezone.utc)


def vid(i, days_ago, sec):
    return {"id": f"v{i}", "snippet": {"title": f"t{i}", "publishedAt": (NOW.replace(day=19) - __import__("datetime").timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")},
            "contentDetails": {"duration": f"PT{sec // 60}M{sec % 60}S"}}


CH = {  # channel id -> (subs, [(days_ago, seconds)])
    "A": (4000, [(2, 3000), (9, 2800), (16, 3100), (23, 2900)] + [(30 + 5 * i, 3000) for i in range(6)]),   # fits
    "B": (4000, [(40, 3000)] * 5),                                       # stale: last upload 40 days ago
    "C": (4000, [(1, 45)] * 8 + [(5, 3000)] * 3),                        # posts mostly shorts
    "D": (900000, [(1, 3000)] * 6),                                      # too big
    "E": (None, [(1, 3000)] * 6),                                        # hidden subscriber count
    "F": (4000, [(3, None)] + [(2 + 6 * i, 3000) for i in range(1, 6)]),  # newest item is a live stream without duration
}


def fake_api(path, p):
    if path == "search":
        return {"items": [{"snippet": {"channelId": c}} for c in CH]}
    if path == "channels":
        return {"items": [{"id": c, "snippet": {"title": f"Chan {c}", "description": "mail me: hi@example.com https://example.com", "customUrl": f"@{c}"},
                           "statistics": ({"hiddenSubscriberCount": True} if CH[c][0] is None else {"subscriberCount": str(CH[c][0])}),
                           "contentDetails": {"relatedPlaylists": {"uploads": "UU" + c}}} for c in p["id"].split(",")]}
    if path == "playlistItems":
        c = p["playlistId"][2:]
        return {"items": [{"contentDetails": {"videoId": f"{c}{i}"}} for i in range(len(CH[c][1]))]}
    if path == "videos":
        out = []
        for vid_id in p["id"].split(","):
            c, i = vid_id[0], int(vid_id[1:]); d, s = CH[c][1][i]
            item = vid(f"{c}{i}", d, s or 0) | {"id": vid_id}
            if s is None:
                item["contentDetails"] = {}; item["snippet"]["liveBroadcastContent"] = "live"
            out.append(item)
        return {"items": out}
    raise AssertionError(path)


def test_filter():
    leads, n = y.run(fake_api, ["q"], days=14, top=30, now=NOW)
    names = [l["channel"] for l in leads]
    assert names == ["Chan A", "Chan F"] or names == ["Chan F", "Chan A"], names
    assert leads[0]["public_email"] == "hi@example.com" and n == 6
    import tempfile
    y.HERE = tempfile.mkdtemp()            # writing must work even when channels have different video counts
    y.write(leads)
    assert os.path.getsize(os.path.join(y.HERE, "leads.csv")) > 0 and "Chan" in open(os.path.join(y.HERE, "leads.md")).read()


def test_duration():
    assert y.iso_seconds("PT1H2M3S") == 3723 and y.iso_seconds("PT45S") == 45 and y.iso_seconds("P0D") == 0


if __name__ == "__main__":
    for k, f in list(globals().items()):
        if k.startswith("test_"):
            f(); print("ok", k)
