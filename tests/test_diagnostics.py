import os
from urllib.parse import parse_qs, urlsplit

from vidgen import diagnostics
from vidgen.diagnostics import (
    KEY_REMOVED, SCRIPT_REMOVED, USER, SessionLog, build_report, issue_url, redact,
)

PEXELS = "pX9kQ2vLm8RtYw3ZaBcD4eFgH5iJ6kL7"
PIXABAY = "12345678-abcdef0123456789abcdef012"
SETTINGS = {
    "api_key": PEXELS, "pixabay_key": PIXABAY, "aspect": "9:16", "resolution": "1080p",
    "voice": "en-US-GuyNeural", "captions": True, "caption_style": "Highlight",
    "music_enabled": False, "ask_save": True, "script": "Voice: my private idea",
    "output_path": r"C:\Users\Deniz\Videos\final_video.mp4",
}
LOG = [
    "🚀 Starting 9:16 render at 1080x1920 (1080p, 8000k)...",
    "🗣️ Generating Guy · Hype Voice: 'My secret business plan is to sell hats.'",
    "🔍 Searching Pixabay for: 'hard drive'...",
    r"✅ DONE! 1080x1920 video saved to: C:\Users\Deniz\Videos\final_video.mp4",
]


# --- redaction -------------------------------------------------------------------

def test_key_values_are_removed_wherever_they_appear():
    text = redact(f"sent {PEXELS} and then {PIXABAY} again {PEXELS}", [PEXELS, PIXABAY])
    assert PEXELS not in text and PIXABAY not in text
    assert text.count(KEY_REMOVED) == 3


def test_keys_in_links_and_headers_are_removed_even_if_unknown():
    text = redact("GET https://pixabay.com/api/videos/?key=zzz999secret&q=cats "
                  "headers={'Authorization': 'abcSECRETdef'}")
    assert "zzz999secret" not in text and "abcSECRETdef" not in text
    assert "q=cats" in text


def test_the_profile_folder_is_hidden_including_short_names():
    text = redact(r"saved to C:\Users\Deniz\Videos\a.mp4 and C:\Users\EXCALI~1\AppData\x "
                  r"and c:/users/Some Body/Desktop")
    assert "Deniz" not in text and "EXCALI~1" not in text
    assert "Some" not in text and "Body" not in text
    assert rf"C:\Users\{USER}\Videos\a.mp4" in text


def test_the_bare_user_name_is_hidden_but_not_inside_other_words():
    text = redact("owner Lab ran it; the Laboratory is fine; LAB again", user_name="Lab")
    assert text == f"owner {USER} ran it; the Laboratory is fine; {USER} again"


def test_spoken_lines_are_removed_but_the_voice_name_stays():
    text = redact(LOG[1])
    assert "hats" not in text and "Guy · Hype" in text and SCRIPT_REMOVED in text


def test_search_words_and_ordinary_text_survive():
    assert redact(LOG[2], [PEXELS], "Deniz") == LOG[2]


def test_empty_or_tiny_secrets_do_not_blank_the_text():
    assert redact("a key is a key", ["", None, "a", "key"]) == "a key is a key"


# --- the report ------------------------------------------------------------------

def test_report_has_what_is_needed_and_nothing_private():
    report = build_report("3.5.0", True, SETTINGS, LOG, "Could not save the video.", "Deniz")
    assert "AI Video Studio 3.5.0 (installed app)" in report
    assert "Could not save the video." in report
    assert "aspect: 9:16" in report and "Pexels key: set" in report
    assert "hard drive" in report
    for private in (PEXELS, PIXABAY, "Deniz", "hats", "my private idea", "output_path"):
        assert private not in report


def test_report_says_when_a_key_is_missing():
    report = build_report("3.5.0", False, dict(SETTINGS, api_key=""), [], "")
    assert "Pexels key: not set" in report and "Pixabay key: set" in report
    assert "run from source" in report and "### Error" not in report


def test_report_keeps_only_the_newest_log_lines():
    lines = [f"line {i}" for i in range(200)]
    report = build_report("3.5.0", True, SETTINGS, lines)
    assert "line 199" in report and "line 140" in report and "line 139" not in report


# --- the issue link --------------------------------------------------------------

def body_of(url):
    return parse_qs(urlsplit(url).query)["body"][0]


def test_issue_link_points_at_this_project_and_carries_the_report():
    report = build_report("3.5.0", True, SETTINGS, LOG, "Boom & <bang>")
    url = issue_url("Render failed: Boom", report)
    assert url.startswith(f"https://github.com/{diagnostics.REPO}/issues/new?")
    assert " " not in url and "\n" not in url
    assert body_of(url) == report
    assert parse_qs(urlsplit(url).query)["title"] == ["Render failed: Boom"]


def test_a_long_report_is_cut_from_the_old_end_to_fit():
    lines = [f"⚠️ step {i} " + "x" * 80 for i in range(60)]
    url = issue_url("Problem", build_report("3.5.0", True, SETTINGS, lines, "Boom"))
    assert len(url) <= diagnostics.URL_LIMIT
    body = body_of(url)
    assert "step 59" in body and "step 0 " not in body
    assert "(earlier lines left out)" in body and "Boom" in body
    assert body.rstrip().endswith("```")


def test_a_report_that_cannot_fit_asks_to_be_pasted():
    url = issue_url("Problem", "y" * 20000)
    assert len(url) <= diagnostics.URL_LIMIT
    assert body_of(url) == diagnostics.PASTE_BODY


# --- the log file ----------------------------------------------------------------

def test_log_lines_are_appended_with_a_timestamp(tmp_path):
    log = SessionLog(str(tmp_path / "logs" / "app.log"))
    log.write("first")
    log.write("second\nthird")
    lines = (tmp_path / "logs" / "app.log").read_text(encoding="utf-8").splitlines()
    assert [line.split("  ", 1)[1] for line in lines] == ["first", "second", "third"]
    assert lines[0][:4].isdigit()


def test_log_rolls_over_and_keeps_one_old_file(tmp_path):
    path = tmp_path / "app.log"
    log = SessionLog(str(path), max_bytes=200)
    for i in range(30):
        log.write(f"line {i} " + "z" * 20)
    assert os.path.exists(str(path) + ".old")
    assert path.stat().st_size < 600
    assert "line 29" in path.read_text(encoding="utf-8")


def test_log_never_raises_when_it_cannot_write(tmp_path):
    wall = tmp_path / "file"
    wall.write_text("x")
    SessionLog(str(wall / "logs" / "app.log")).write("ignored")   # must not raise
