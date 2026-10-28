"""Home's Get started list and Your videos numbers (vidgen.started)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from vidgen import started  # noqa: E402

NOW = 1_800_000_000.0


def test_a_broken_saved_list_starts_empty():
    assert started.state(None) == {"done": [], "skipped": [], "hidden": False}
    assert started.state({"done": ["tip", "nonsense"], "skipped": "x", "hidden": 1}) == \
        {"done": ["tip"], "skipped": [], "hidden": True}


def test_my_voice_is_only_listed_where_it_can_run():
    assert "myvoice" in dict(started.steps(True))
    assert "myvoice" not in dict(started.steps(False))


def test_steps_are_done_when_seen_or_true_now_and_skipped_ones_count():
    saved = started.state({"done": ["tip"], "skipped": ["rewrite"]})
    facts = {"key": True, "first": False, "myvoice": False}
    assert started.finished(saved, facts) == {"tip", "key"}
    assert started.progress(saved, facts, started.steps(False)) == (3, 5)


def test_summary_counts_videos_not_drafts():
    projects = [
        {"title": "Night light", "videos": ["a.mp4"], "created": NOW - 3600, "took": 40},
        {"title": "Draft", "videos": [], "created": NOW - 60},
        {"title": "Old", "videos": ["b.mp4"], "created": NOW - 30 * 86400, "took": 60},
        {"title": "Older", "videos": ["c.mp4"], "created": NOW - 40 * 86400, "took": 50},
    ]
    numbers = started.summary(projects, NOW)
    assert numbers["made"] == 3 and numbers["week"] == 1
    assert numbers["last"]["title"] == "Night light" and numbers["usual"] == 50


def test_summary_of_nothing():
    assert started.summary([], NOW) == {"made": 0, "week": 0, "last": None, "usual": None}


def test_ago_reads_naturally():
    assert started.ago(NOW - 10, NOW) == "just now"
    assert started.ago(NOW - 300, NOW) == "5 min ago"
    assert started.ago(NOW - 3 * 3600, NOW) == "3 hours ago"
    assert started.ago(NOW - 86400, NOW) == "1 day ago"


def test_greeting_follows_the_clock():
    assert started.greeting(8) == "Good morning"
    assert started.greeting(14) == "Good afternoon"
    assert started.greeting(21) == "Good evening" and started.greeting(2) == "Good evening"


def test_status_line():
    numbers = started.summary([{"title": "A", "videos": ["a"], "created": NOW - 60,
                                "updated": NOW - 3 * 3600}], NOW)
    assert started.status_line(numbers, NOW) == "1 video this week, last one 3 hours ago."
    assert "first video" in started.status_line(started.summary([], NOW), NOW)


def test_tip_ideas_are_the_same_all_day_and_skip_made_ones():
    tips = [(f"Tip number {i}", "p") for i in range(20)]
    today = started.tip_ideas(tips, "", 740000)
    assert len(today) == 3 and today == started.tip_ideas(tips, "", 740000)
    made = today[0][0].lower()
    assert today[0] not in started.tip_ideas(tips, made, 740000)


def test_whats_new_shows_for_a_week_after_an_update():
    since = {"version": "4.8.0", "at": NOW - 86400}
    assert started.whats_new_due(since, "4.8.0", NOW)
    assert not started.whats_new_due(since, "4.8.1", NOW)
    assert not started.whats_new_due({"version": "4.8.0", "at": NOW - 8 * 86400}, "4.8.0", NOW)
    assert not started.whats_new_due(None, "4.8.0", NOW)


def test_dashboard_numbers():
    made = [{"videos": ["a"], "created": NOW - 60, "length": 90, "settings": {"voice": "guy"}},
            {"videos": ["b"], "created": NOW - 8 * 86400, "length": 30,
             "settings": {"voice": "guy"}},
            {"videos": ["c"], "created": NOW - 9 * 86400, "length": 60,
             "settings": {"voice": "ava"}},
            {"videos": [], "created": NOW, "length": 500, "settings": {"voice": "ava"}}]
    assert started.per_week(made, NOW) == [0, 0, 0, 0, 0, 0, 2, 1]
    assert started.minutes(made) == 3.0
    assert started.most_used(made, "voice") == "guy"
    assert started.per_week([], NOW) == [0] * 8 and started.most_used([], "voice") is None
