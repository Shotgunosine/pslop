import pytest

from conftest import NARRATIVE, PLAN, REPO
from pmap_calibrate.blocks import parse_blocks, sentences
from pmap_calibrate.loaders import load_text
from pmap_calibrate.narrative import split_items
from pmap_calibrate.plan import Threshold, parse_plan, parse_threshold
from pmap_calibrate.questions import counted_text


def test_parse_plan():
    elements = parse_plan(PLAN)
    assert [e.name for e in elements] == ["HHS Customer Experience (CX)", "Scientific Research and Innovation"]
    cx, research = elements
    assert cx.description == "Customers are internal and external."
    assert [s.text for s in cx.at_level(5)] == [
        "Routinely responds to customer inquiries within one business day.",
        "Serves as a trusted advisor to stakeholders.",
    ]
    assert [s.text for s in research.at_level(3)] == [
        "Carries out research projects of these types: new data analysis methods; support projects"
    ]
    assert research.at_level(5)[0].threshold == Threshold(">", 2)
    assert research.at_level(5)[1].threshold is None


def test_parse_real_plan():
    path = REPO / "PMAP_MLC_2026.md"
    if not path.exists():
        pytest.skip("sample plan not present")
    elements = parse_plan(load_text(path))
    assert len(elements) == 5
    research = elements[1]
    assert research.name == "Scientific Research and Innovation"
    assert [s.threshold for s in research.at_level(5)[:2]] == [Threshold(">", 2), Threshold(">=", 4)]
    service = elements[4].at_level(5)
    assert service[0].text.startswith("Provides service in support of the NIH intramural research enterprise")
    assert "organization of research workshops or seminar series" in service[0].text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Advances more than two research projects.", Threshold(">", 2)),
        ("Provides advice on at least four (4) projects.", Threshold(">=", 4)),
        ("Presents in one or more seminars.", Threshold(">=", 1)),
        ("Responds within one (1) business day.", None),
    ],
)
def test_parse_threshold(text, expected):
    assert parse_threshold(text) == expected


def test_counted_text_drops_the_number():
    [_, research] = parse_plan(PLAN)
    assert counted_text(research.at_level(5)[0]) == "Advances research projects to key milestones."


def test_split_items():
    items, skipped = split_items(load_text_from(NARRATIVE))
    assert skipped == 1  # the "Calibration factor key" line
    assert [i.content[:20] for i in items] == [
        "Served as analytic a", "Advanced three proje", "Resting-state projec", "Cognition project fi",
        "Developed a new netw",
    ]
    assert items[0].tags == {"TE"} and "[TE]" not in items[0].content
    assert [i.parent for i in items] == [None, None, 1, 1, None]
    assert items[1].section == "Scientific Research and Innovation"


def test_prose_narrative_uses_sentences():
    items, skipped = split_items("I led the review of five protocols. I also trained two new staff members.")
    assert [i.content for i in items] == ["I led the review of five protocols.", "I also trained two new staff members."]
    assert skipped == 0


def test_blocks_wrap_pdf_style_lines():
    [block] = parse_blocks("• Advances more than two research\nprojects to key milestones.")
    assert block.text == "Advances more than two research projects to key milestones."


def test_sentences_keep_abbreviations():
    assert sentences("Uses tools (e.g. Python) well. Shares data.") == ["Uses tools (e.g. Python) well.", "Shares data."]


def load_text_from(markdown: str) -> str:
    # Mirrors load_text for Markdown: remove backslash escapes.
    return markdown.replace("\\[", "[").replace("\\]", "]")
