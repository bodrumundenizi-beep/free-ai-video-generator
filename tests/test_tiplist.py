"""The ready-made tips: every page has one, and each leads the map to its own page."""
import pytest

from vidgen import tiplist, winmap


@pytest.mark.parametrize("text, place_id", tiplist.TIPS)
def test_each_tip_leads_to_its_own_page(text, place_id):
    assert winmap.search(text)[0].id == place_id


def test_every_page_but_admin_ones_has_a_tip_and_none_twice():
    covered = {place_id for _text, place_id in tiplist.TIPS}
    assert covered == {p.id for p in winmap.PLACES if not p.admin}
    texts = [text.lower() for text, _p in tiplist.TIPS]
    assert len(texts) == len(set(texts))


def test_the_picker_groups_them_all():
    groups = tiplist.groups()
    assert sum(len(tips) for _name, tips in groups) == len(tiplist.TIPS)
    assert [name for name, _t in groups][:2] == ["Speed", "Gaming"]
