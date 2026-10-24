import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from automation.instagram_slot_publisher import MIN_GAP_MINUTES, pick_due  # noqa: E402

NOW = datetime(2026, 10, 14, 9, 5, tzinfo=timezone.utc)


def _entry(task_id, ig):
    return {"task_id": task_id, "platforms": {"instagram": ig}}


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_nothing_due_before_slot():
    entries = [_entry("a", {"status": "pending_slot", "scheduled_for": _iso(NOW + timedelta(hours=1))})]
    assert pick_due(entries, NOW)[0] is None


def test_due_entry_is_picked_oldest_first():
    entries = [
        _entry("late", {"status": "pending_slot", "scheduled_for": _iso(NOW - timedelta(minutes=5))}),
        _entry("early", {"status": "pending_slot", "scheduled_for": _iso(NOW - timedelta(hours=3))}),
    ]
    assert pick_due(entries, NOW)[0]["task_id"] == "early"


def test_min_gap_blocks_a_burst():
    recent = _iso(NOW - timedelta(minutes=MIN_GAP_MINUTES - 5))
    entries = [
        _entry("done", {"status": "live", "id": "1", "posted_at": recent}),
        _entry("due", {"status": "pending_slot", "scheduled_for": _iso(NOW - timedelta(minutes=1))}),
    ]
    entry, reason = pick_due(entries, NOW)
    assert entry is None and "min gap" in reason


def test_gap_passed_allows_next():
    old = _iso(NOW - timedelta(minutes=MIN_GAP_MINUTES + 5))
    entries = [
        _entry("done", {"status": "live", "id": "1", "posted_at": old}),
        _entry("due", {"status": "pending_slot", "scheduled_for": _iso(NOW - timedelta(minutes=1))}),
    ]
    assert pick_due(entries, NOW)[0]["task_id"] == "due"


def test_fresh_claim_is_skipped_stale_claim_is_retried():
    fresh = _entry("x", {"status": "publishing", "claimed_at": NOW.isoformat(),
                         "scheduled_for": _iso(NOW - timedelta(minutes=30))})
    assert pick_due([fresh], NOW)[0] is None
    stale = _entry("y", {"status": "publishing", "claimed_at": (NOW - timedelta(hours=2)).isoformat(),
                         "scheduled_for": _iso(NOW - timedelta(hours=3))})
    assert pick_due([stale], NOW)[0]["task_id"] == "y"
