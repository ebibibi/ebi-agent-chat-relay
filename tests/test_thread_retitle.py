"""Tests for thread_retitle — deciding *when* a thread deserves a new title."""

from __future__ import annotations

from claude_discord.discord_ui.thread_retitle import RetitlePolicy, RetitleTracker


def _tracker(**kwargs) -> RetitleTracker:
    return RetitleTracker(policy=RetitlePolicy(**kwargs))


class TestClaimTiming:
    def test_not_due_before_enough_messages(self):
        t = _tracker(min_interval_seconds=0, messages_before_retitle=3)
        t.record(1, "first", now=0.0)
        t.record(1, "second", now=1.0)
        assert t.claim(1, now=2.0) is None

    def test_due_once_both_thresholds_are_met(self):
        t = _tracker(min_interval_seconds=10, messages_before_retitle=2)
        t.record(1, "first", now=0.0)
        t.record(1, "second", now=20.0)
        assert t.claim(1, now=20.0) == ("first", "second")

    def test_not_due_before_the_cooldown_elapses(self):
        """Discord allows two renames per ten minutes — never race that limit."""
        t = _tracker(min_interval_seconds=900, messages_before_retitle=1)
        t.record(1, "first", now=0.0)
        t.record(1, "second", now=60.0)
        assert t.claim(1, now=60.0) is None

    def test_claim_resets_both_counters(self):
        t = _tracker(min_interval_seconds=10, messages_before_retitle=2)
        t.record(1, "a", now=0.0)
        t.record(1, "b", now=20.0)
        assert t.claim(1, now=20.0) is not None
        # Immediately after a claim the thread is quiet again.
        t.record(1, "c", now=21.0)
        t.record(1, "d", now=40.0)
        assert t.claim(1, now=40.0) == ("c", "d")

    def test_second_claim_in_the_same_window_is_refused(self):
        t = _tracker(min_interval_seconds=10, messages_before_retitle=1)
        t.record(1, "a", now=0.0)
        t.record(1, "b", now=20.0)
        assert t.claim(1, now=20.0) is not None
        assert t.claim(1, now=20.0) is None

    def test_unknown_thread_is_never_due(self):
        assert _tracker().claim(999, now=10_000.0) is None

    def test_threads_are_tracked_independently(self):
        t = _tracker(min_interval_seconds=10, messages_before_retitle=2)
        t.record(1, "one", now=0.0)
        t.record(2, "two", now=0.0)
        t.record(1, "one again", now=20.0)
        assert t.claim(1, now=20.0) == ("one", "one again")
        assert t.claim(2, now=20.0) is None


class TestRecording:
    def test_blank_messages_are_ignored(self):
        t = _tracker(min_interval_seconds=0, messages_before_retitle=1)
        t.record(1, "   ", now=0.0)
        assert t.claim(1, now=100.0) is None

    def test_keeps_only_the_most_recent_messages(self):
        t = _tracker(min_interval_seconds=0, messages_before_retitle=1, recent_messages_kept=2)
        for i, text in enumerate(["a", "b", "c"]):
            t.record(1, text, now=float(i))
        assert t.claim(1, now=10.0) == ("b", "c")

    def test_long_messages_are_truncated(self):
        t = _tracker(min_interval_seconds=0, messages_before_retitle=1, message_chars_kept=5)
        t.record(1, "x" * 50, now=0.0)
        claimed = t.claim(1, now=10.0)
        assert claimed == ("xxxxx",)

    def test_forget_drops_a_thread(self):
        t = _tracker(min_interval_seconds=0, messages_before_retitle=1)
        t.record(1, "a", now=0.0)
        t.forget(1)
        assert t.claim(1, now=10.0) is None

    def test_tracker_evicts_the_oldest_thread_beyond_its_bound(self):
        t = RetitleTracker(policy=RetitlePolicy(min_interval_seconds=0), max_threads=2)
        t.record(1, "a", now=0.0)
        t.record(2, "b", now=0.0)
        t.record(3, "c", now=0.0)
        assert t.tracked_thread_ids() == (2, 3)
