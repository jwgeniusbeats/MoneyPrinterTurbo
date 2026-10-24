import json
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from automation import export_status as ex  # noqa: E402
from automation import reconcile_tiktok_status as rc  # noqa: E402


def _entry(path, tt="pending_manual", title="t"):
    return {"title": title, "video_path": path, "platforms": {"tiktok": {"status": tt}}}


class ChannelFeedTests(unittest.TestCase):
    def test_feed_has_no_channel_key_when_nothing_is_known(self):
        self.assertNotIn("channel", ex.build_feed([], {}))
        self.assertNotIn("channel", ex.build_feed([]))

    def test_feed_includes_channel_when_given(self):
        ch = {"asOf": "x", "youtube": {"subscribers": 26}}
        self.assertEqual(ex.build_feed([], ch)["channel"], ch)

    def test_collect_channel_omits_what_fails_and_never_guesses(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(ex, "TIKTOK_FOLLOWERS_FILE", os.path.join(d, "missing.json")):
                # geen youtube_api bereikbaar (import faalt in de testomgeving) en geen followers-bestand
                with patch.dict(sys.modules, {"app.services.youtube_api": None}):
                    self.assertEqual(ex.collect_channel(), {})

    def test_collect_channel_reads_tiktok_followers_with_their_own_timestamp(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "followers.json")
            json.dump({"followers": 2, "updated_at": "2026-10-16T10:00:00Z"}, open(f, "w"))
            fake_yt = types.SimpleNamespace(
                get_channel_stats=lambda: {"subscriberCount": "26"},
                get_watch_minutes=lambda days: 1200.0,
            )
            fake_services = types.ModuleType("app.services")
            fake_services.youtube_api = fake_yt
            with patch.object(ex, "TIKTOK_FOLLOWERS_FILE", f), \
                    patch.dict(sys.modules, {"app.services": fake_services, "app.services.youtube_api": fake_yt}):
                ch = ex.collect_channel()
        self.assertEqual(ch["youtube"], {"subscribers": 26, "watchHours365": 20.0})
        self.assertEqual(ch["tiktok"], {"followers": 2, "asOf": "2026-10-16T10:00:00Z"})
        self.assertIn("asOf", ch)
        for secret in ("token", "channel_id", "/Users"):
            self.assertNotIn(secret, json.dumps(ch))

    def test_commit_tree_gets_the_fixed_identity(self):
        calls = []

        def fake_run(cmd, input=None, capture_output=None, text=None, check=None, env=None):
            calls.append((cmd, env))
            return types.SimpleNamespace(stdout="abc123\n")

        with patch.object(ex.subprocess, "run", side_effect=fake_run):
            ex.push_feed("/tmp/x.json")
        commit_calls = [c for c in calls if "commit-tree" in c[0]]
        self.assertEqual(len(commit_calls), 1)
        env = commit_calls[0][1]
        self.assertEqual(env["GIT_AUTHOR_EMAIL"], "noreply@anthropic.com")
        self.assertEqual(env["GIT_COMMITTER_NAME"], "Claude")
        # de force-push naar status-feed blijft ongewijzigd
        self.assertTrue(any("push" in c[0] and "--force" in c[0] for c in calls))


class ReconcileTests(unittest.TestCase):
    def test_skipped_lines_never_count(self):
        lines = ["2026-10-06T10:00:00Z SKIPPED /a/F1/v.mp4\n", "2026-10-11T09:05:00Z /a/F2/v.mp4\n"]
        posted = rc.parse_posted_log(lines)
        self.assertEqual([p for p, _ in posted], ["/a/F2/v.mp4"])
        self.assertEqual(posted[0][1][:10], "2026-10-11")

    def test_only_pending_entries_with_a_unique_match_are_updated(self):
        entries = [_entry("/mac/Videos/F1/v.mp4"), _entry("/mac/Videos/F2/v.mp4", tt="live"), _entry("/mac/Videos/F3/v.mp4")]
        posted = [("/mac/Videos/F1/v.mp4", None), ("/mac/Videos/F2/v.mp4", None), ("/other/F3/v.mp4", None)]
        updates, unknown, ambiguous = rc.plan(entries, posted)
        self.assertEqual([i for i, _, _ in updates], [0, 2])  # F2 was al live, F3 gematcht op mapnaam
        self.assertEqual((unknown, ambiguous), ([], []))

    def test_unknown_and_ambiguous_paths_are_not_guessed(self):
        entries = [_entry("/m/F1/a.mp4"), _entry("/n/F1/b.mp4")]
        updates, unknown, ambiguous = rc.plan(entries, [("/x/F1/c.mp4", None), ("/x/F9/c.mp4", None)])
        self.assertEqual(updates, [])
        self.assertEqual(ambiguous, ["/x/F1/c.mp4"])
        self.assertEqual(unknown, ["/x/F9/c.mp4"])


if __name__ == "__main__":
    unittest.main()
