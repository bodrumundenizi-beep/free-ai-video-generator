"""The tip tester's own rules: which places, pass or fail, kept between runs, the summary."""
from vidgen import tiptest, winmap


def test_tip_text_names_the_place():
    assert tiptest.tip_text(winmap.BY_ID["nightlight"]) == "how to change Night light"


def test_places_by_range_or_by_id():
    places = winmap.PLACES
    assert tiptest.choose(places, "1-30") == list(places[:30])
    assert tiptest.choose(places, "31-110")[0] is places[30]
    assert len(tiptest.choose(places, "111-198")) == len(places) - 110
    assert [p.id for p in tiptest.choose(places, only="colors, nightlight")] == \
        [p.id for p in places if p.id in ("colors", "nightlight")]
    assert tiptest.choose(places) == list(places)


def test_pass_and_fail_rules():
    night, firewall = winmap.BY_ID["nightlight"], winmap.BY_ID["firewall"]
    assert tiptest.verdict(night, "", "nightlight", 6.0) == ("pass", "")
    assert tiptest.verdict(night, "", "colors", 6.0)[1] == "the map chose another page: colors"
    assert tiptest.verdict(night, "", "search", 6.0)[0] == "fail"
    assert tiptest.verdict(night, "", "nightlight", 0.0) == ("fail", "no video was made")
    assert tiptest.verdict(night, "Couldn't open the window", "", 0)[0] == "fail"
    # Admin places must be refused, not recorded.
    refused = "Windows Defender Firewall needs admin rights or guards your PC's security"
    assert tiptest.verdict(firewall, refused, "", 0) == ("pass", "refused, as it should be")
    assert tiptest.verdict(firewall, "", "firewall", 6.0)[0] == "fail"


def test_results_are_kept_and_a_new_run_skips_what_is_done(tmp_path):
    results = tiptest.Results(str(tmp_path))
    results.record("nightlight", result="pass", reason="", attempts=1, seconds=60.0, video="x")
    results.record("colors", result="fail", reason="stuck", attempts=2, seconds=480.0, video="")
    again = tiptest.Results(str(tmp_path))
    assert again.done("nightlight") and again.done("colors") and not again.done("sound")


def test_the_summary_lists_failed_ones_first(tmp_path):
    results = tiptest.Results(str(tmp_path))
    results.record("nightlight", result="pass", reason="", attempts=1, seconds=60.0, video="x")
    results.record("colors", result="fail", reason="stuck while planning", attempts=2,
                   seconds=480.0, video="")
    text = tiptest.summary(results, list(winmap.PLACES))
    assert text.startswith("# Tip test: 1 passed, 1 failed, 2 of 198 places tested") or \
        "1 passed, 1 failed, 2 of" in text.splitlines()[0]
    failed_at, all_at = text.index("## Failed"), text.index("## All tested")
    assert failed_at < all_at and "stuck while planning" in text[failed_at:all_at]
    assert "Night light (`nightlight`) | pass" in text
