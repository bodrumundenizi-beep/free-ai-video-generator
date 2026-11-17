import requests

from vidgen import webfacts


def test_which_ideas_are_looked_up():
    assert webfacts.wants_facts("top 5 AMD GPUs") and webfacts.wants_facts("fastest cars in 2025")
    assert not webfacts.wants_facts("write a scary story about being followed at night")
    assert not webfacts.wants_facts("why cats sleep so much")
    assert webfacts.search_words("make a video about the top 5 AMD GPUs") == "AMD GPUs"
    assert webfacts.wants_recent("newest iPhone") and not webfacts.wants_recent("tallest buildings")


def test_tables_become_text_and_the_newest_rows_are_chosen():
    page = ("<p>Intro about cars.</p><style>.x{}</style><h2>Records</h2><table>"
            "<tr><td>Old car</td><td>1990</td></tr><tr><td>New car</td><td>2025</td></tr></table>")
    parts = webfacts.sections(page)
    assert parts[0] == "Intro about cars." and "Old car | 1990" in parts[1]
    assert ".x{}" not in "".join(parts)
    picked = webfacts.choose(parts, 34, recent=True)
    assert "New car" in picked and "Old car" not in picked


class Http:
    def __init__(self, fail=False):
        self.fail = fail

    def get(self, url, params=None, headers=None, timeout=None):
        assert timeout and "AIVideoStudio" in headers["User-Agent"]
        if self.fail:
            raise requests.ConnectionError()
        answer = ({"query": {"search": [{"title": "List of cars"}]}} if params.get("list")
                  else {"parse": {"text": {"*": "<p>Fast car | 500 km/h</p>"}}})
        return type("R", (), {"json": lambda self: answer})()


def test_a_year_stays_and_the_idea_s_first_part_is_searched_too():
    idea = "the conquest of Istanbul in 1453, when Sultan Mehmed II took Constantinople"
    assert webfacts.queries(idea) == [
        "conquest of Istanbul in 1453",
        "conquest of Istanbul in 1453, when Sultan Mehmed II took Constantinople"]
    assert webfacts.queries("top 5 AMD GPUs") == ["AMD GPUs"]


class TwoSearches(object):
    def get(self, url, params=None, headers=None, timeout=None):
        if params.get("list"):
            top = "Fall of Constantinople" if "when" not in params["srsearch"] else "Mehmed II"
            answer = {"query": {"search": [{"title": top}, {"title": "Fetih 1453"}]}}
        else:   # a plain-text extract: no infobox, no tables
            answer = {"query": {"pages": {"1": {"extract": (
                f"About {params['titles']}, in 1453.\n\n\n== Siege ==\nThe siege took 53 "
                "days.\n\n\n== References ==\nA book.")}}}}
        return type("R", (), {"json": lambda self: answer})()


def test_the_top_page_of_each_search_comes_first_as_plain_sentences():
    text, titles = webfacts.facts("the conquest of Istanbul in 1453, when Sultan Mehmed II "
                                  "took Constantinople", TwoSearches())
    assert titles == ["Fall of Constantinople", "Mehmed II"]
    assert "About Fall of Constantinople, in 1453." in text and "Siege: The siege took 53" in text
    assert "A book" not in text   # reference lists left out


def test_facts_come_back_with_their_page_or_nothing_offline():
    text, titles = webfacts.facts("fastest cars", Http())
    assert titles == ["List of cars"] and "Fast car" in text
    assert webfacts.facts("fastest cars", Http(fail=True)) == ("", [])
