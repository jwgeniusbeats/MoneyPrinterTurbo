import os
import random
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from automation import experiments as ex  # noqa: E402

NOW = datetime(2026, 10, 22, tzinfo=timezone.utc)


def entry(length, views, days_old=5, subtitles="sentence", yt_pct=None):
    yt = {"views": views}
    if yt_pct is not None:
        yt["avg_view_percentage"] = yt_pct
    return {
        "posted_at": (NOW - timedelta(days=days_old)).isoformat(),
        "experiment": {"length": length, "subtitles": subtitles},
        "platforms": {"youtube": yt, "tiktok": {"views": views}},
    }


class ExperimentTests(unittest.TestCase):
    def test_default_is_fifty_fifty(self):
        w = ex.weights_for("length", {})
        self.assertEqual(w, {"short": 0.5, "long": 0.5})

    def test_choose_arms_covers_every_variable_and_is_valid(self):
        arms = ex.choose_arms(random.Random(1), {})
        self.assertEqual(set(arms), set(ex.EXPERIMENTS))
        for var, arm in arms.items():
            self.assertIn(arm, ex.EXPERIMENTS[var])

    def test_weights_stay_even_below_min_samples(self):
        entries = [entry("short", 900) for _ in range(ex.MIN_SAMPLES_PER_ARM - 1)]
        entries += [entry("long", 10) for _ in range(ex.MIN_SAMPLES_PER_ARM - 1)]
        res = ex.analyse(entries, now=NOW)
        self.assertFalse(res["length"]["enough_data"])
        self.assertEqual(res["length"]["weights"], {"short": 0.5, "long": 0.5})

    def test_winner_gets_more_weight_but_floor_holds(self):
        n = ex.MIN_SAMPLES_PER_ARM + 4
        entries = [entry("short", 1000 + i) for i in range(n)] + [entry("long", 10 + i) for i in range(n)]
        res = ex.analyse(entries, now=NOW)
        w = res["length"]["weights"]
        self.assertTrue(res["length"]["enough_data"])
        self.assertGreater(w["short"], w["long"])
        self.assertGreaterEqual(w["long"], ex.EXPLORATION_FLOOR / (1 + ex.EXPLORATION_FLOOR) - 1e-9)
        self.assertAlmostEqual(sum(w.values()), 1.0)

    def test_immature_and_untagged_entries_are_ignored(self):
        young = entry("short", 5000, days_old=1)
        untagged = {"posted_at": (NOW - timedelta(days=9)).isoformat(), "platforms": {"tiktok": {"views": 9}}}
        self.assertEqual(ex.rewards([young, untagged], now=NOW), [])

    def test_update_writes_state_and_report_and_feeds_choose_arms(self):
        n = ex.MIN_SAMPLES_PER_ARM + 4
        entries = [entry("long", 1000 + i) for i in range(n)] + [entry("short", 10 + i) for i in range(n)]
        with tempfile.TemporaryDirectory() as d:
            sp, rp = os.path.join(d, "s.json"), os.path.join(d, "r.md")
            ex.update_from_post_log(entries, state_path=sp, report_path=rp, now=NOW)
            self.assertTrue(os.path.exists(sp))
            self.assertIn("length", open(rp, encoding="utf-8").read())
            state = ex._load_state(sp)
            picks = [ex.choose_arms(random.Random(i), state)["length"] for i in range(400)]
            self.assertGreater(picks.count("long"), picks.count("short"))
            self.assertGreater(picks.count("short"), 0)

    def test_broken_state_file_falls_back(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "bad.json")
            open(p, "w").write("{nope")
            self.assertEqual(ex._load_state(p), {})

    def test_length_instruction_fallback(self):
        self.assertEqual(ex.length_instruction("short"), "Write 45-55 words.")
        self.assertEqual(ex.length_instruction(None), ex.DEFAULT_LENGTH_INSTRUCTION)


if __name__ == "__main__":
    unittest.main()
