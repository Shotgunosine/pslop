import pytest

from conftest import NARRATIVE, PLAN, FakeJev
from pmap_calibrate.assess import EmployeeCase, match_ratings, prepare, run
from pmap_calibrate.decide import NOT_ASSESSED, NOT_SUPPORTED, SUPPORTED, UNDER_RATED, Rule, decide, verdict
from pmap_calibrate.jev import ChoiceAnswer, NoulAnswer
from pmap_calibrate.narrative import split_items
from pmap_calibrate.plan import parse_plan

ELEMENTS = parse_plan(PLAN)
ITEMS = split_items(NARRATIVE.replace("\\[", "[").replace("\\]", "]"))[0]
CX, RESEARCH = ELEMENTS


def answers(nouls=None, counts=None, default=0.1):
    """Build per-item answers: nouls[(item, key)] = p; counts[(item, key)] = option."""
    nouls, counts = nouls or {}, counts or {}
    result = {}
    for item in ITEMS:
        a = {}
        for element in ELEMENTS:
            for s in element.standards:
                if s.threshold:
                    choice = counts.get((item.index, s.id), "none")
                    a[f"cnt_{s.id}"] = ChoiceAnswer(type="choice", choice=choice, probabilities={"none": 0.9 if choice == "none" else 0.05})
                else:
                    a[f"std_{s.id}"] = NoulAnswer(type="noul", noul=nouls.get((item.index, s.id), default))
        for key in ("fac_OV", "fac_QR", "fac_TE", "flag_specific", "flag_personal", "flag_directive"):
            a[key] = NoulAnswer(type="noul", noul=nouls.get((item.index, key), default if key.startswith("fac") else 0.9 if key != "flag_directive" else 0.05))
        result[item.index] = a
    return result


def test_standard_met_by_best_item_with_evidence():
    a = answers({(0, "e1_l5_1"): 0.92, (4, "e1_l5_1"): 0.55})
    d = decide(ELEMENTS, ITEMS, a, {"e1": 5, "e2": 5})
    routinely = d.elements[0].level5[0]
    assert routinely.met and routinely.best == 0.92 and not routinely.uncertain
    assert routinely.evidence == [(0, 0.92), (4, 0.55)]
    assert not d.elements[0].level5[1].met


def test_counted_standard_counts_parent_or_children_once():
    # Parent says three projects; its two sub-bullets say one each: the group counts max(3, 2) = 3.
    a = answers(counts={(1, "e2_l5_1"): "three", (2, "e2_l5_1"): "one", (3, "e2_l5_1"): "one"})
    result = decide(ELEMENTS, ITEMS, a, {"e2": 5}).elements[1].level5[0]
    assert result.count == 3 and result.met
    assert result.uncertain  # one fewer would fail "more than two"
    assert result.counts == [(1, 3), (2, 1), (3, 1)]

    a = answers(counts={(2, "e2_l5_1"): "two", (3, "e2_l5_1"): "two", (4, "e2_l5_1"): "one"})
    result = decide(ELEMENTS, ITEMS, a, {"e2": 5}).elements[1].level5[0]
    assert result.count == 5 and result.met and not result.uncertain


def test_element_levels_and_verdicts():
    # CX: 1 of 2 Level 5 standards met (needs 1) -> Level 5. Research: 0 of 2 -> Level 3.
    a = answers({(0, "e1_l5_1"): 0.95})
    d = decide(ELEMENTS, ITEMS, a, {"e1": 5, "e2": 5})
    assert [(e.supported, e.verdict) for e in d.elements] == [(5, SUPPORTED), (3, NOT_SUPPORTED)]
    assert d.supported_summary.level == 3 and d.verdict == NOT_SUPPORTED
    assert d.level5_evidence == 0.25

    d = decide(ELEMENTS, ITEMS, a, {"e1": 3, "e2": 3})
    assert d.elements[0].verdict == UNDER_RATED


def test_level5_share_rule():
    a = answers({(0, "e1_l5_1"): 0.95})
    d = decide(ELEMENTS, ITEMS, a, {"e1": 5}, Rule(level5_share=1.0))
    assert d.elements[0].level5_needed == 2 and d.elements[0].supported == 3


def test_borderline():
    # CX's one met standard sits just above the threshold, so CX (and the summary) could go either way.
    a = answers({(0, "e1_l5_1"): 0.55, (4, "e2_l5_2"): 0.95})
    d = decide(ELEMENTS, ITEMS, a, {"e1": 5, "e2": 5})
    assert d.elements[0].supported == 5 and d.elements[0].borderline
    assert d.elements[1].supported == 5 and not d.elements[1].borderline
    assert d.borderline

    d = decide(ELEMENTS, ITEMS, answers({(0, "e1_l5_1"): 0.95, (4, "e2_l5_2"): 0.95}), {"e1": 5, "e2": 5})
    assert not d.borderline


def test_factors_and_flags():
    a = answers({(0, "fac_TE"): 0.9, (1, "fac_QR"): 0.2, (4, "flag_specific"): 0.2, (2, "flag_directive"): 0.8})
    d = decide(ELEMENTS, ITEMS, a, {"e1": 5})
    te = next(f for f in d.factors if f.factor.key == "TE")
    qr = next(f for f in d.factors if f.factor.key == "QR")
    assert te.evidence == [(0, 0.9)]
    assert qr.tagged_unsupported == [1]  # tagged [QR] but Jev gives 0.2
    assert [i for i, _ in d.flags["vague"]] == [4]
    assert [i for i, _ in d.flags["directive"]] == [2]


def test_verdict():
    assert verdict(5, 5) == SUPPORTED
    assert verdict(1, 3) == NOT_ASSESSED
    assert verdict(5, None) == NOT_ASSESSED


def test_match_ratings():
    matched, unmatched = match_ratings(
        [("Customer Experience", 5), ("Research", 3), ("Underwater Basketry", 5)], ELEMENTS
    )
    assert matched == {"e1": 5, "e2": 3}
    assert unmatched == ["Underwater Basketry"]


def _case(case_files, ratings=(("Customer Experience", 5), ("Scientific Research", 5)), names=True, supervisor=None):
    return EmployeeCase(
        narrative_path=case_files["narrative"], plan_path=case_files["plan"], ratings=list(ratings),
        names_paths=[case_files["names"]] if names else [], supervisor_path=supervisor,
    )


def test_prepare_sends_one_item_per_request_without_ratings_or_names(case_files):
    prepared = prepare(_case(case_files), "typesafe/jev-1.13")
    assert len(prepared.requests) == len(prepared.items) == 5
    body = prepared.requests[0][1]
    assert body["state"] == {"accomplishment": "Served as analytic advisor to Dr. [PERSON_1]'s branch, always responding within one business day."}
    assert body["provider"] == {"data_collection": "deny", "zdr": True}
    sent = str([b for _, b in prepared.requests])
    assert "Pine" not in sent and "Lakey" not in sent and "Level 5 (" not in sent
    assert {k.split("_")[0] for k in body["questions"]} == {"std", "cnt", "fac", "flag"}


def test_prepare_rejects_unmatched_rating(case_files):
    with pytest.raises(ValueError, match="Could not match 'Basketry'"):
        prepare(_case(case_files, ratings=(("Basketry", 5),)), "m")


def test_rating_official_items_skip_flags_and_counts(case_files, tmp_path):
    supervisor = tmp_path / "supervisor.md"
    supervisor.write_text("* The employee led three projects to milestones this year.\n", encoding="utf-8")
    prepared = prepare(_case(case_files, supervisor=supervisor), "m")
    index, body = prepared.requests[-1]
    assert prepared.items[index].source == "rating official"
    assert not any(k.startswith("flag_") for k in body["questions"])
    # Its count answers are ignored so the same projects aren't counted twice.
    jev = FakeJev(p=0.1, count="none",
                  rule=lambda key, text: "five_or_more" if key == "cnt_e2_l5_1" and "led three" in text else None)
    result = run(prepared, jev)
    assert result.decision.elements[1].level5[0].count == 0


def test_run_end_to_end(case_files):
    jev = FakeJev(p=0.9, count="two")
    result = run(prepare(_case(case_files), "typesafe/jev-1.13"), jev)
    d = result.decision
    assert d.verdict == SUPPORTED and [e.supported for e in d.elements] == [5, 5]
    # Items 0 and 4 count 2 each; item 1's group counts max(2, 2 + 2) = 4.
    assert d.elements[1].level5[0].count == 8
    assert result.audit["models_used"] == ["typesafe/jev-1.13-20260917"]
    assert result.audit["requests"] == 5 and result.audit["cost_usd"] == 0.0005
    assert result.audit["privacy"]["proposed_ratings_sent"] is False
    assert result.answers[0]["fac_TE"] == {"type": "noul", "noul": 0.9}


def test_simple_prepare_sends_the_whole_narrative_once(case_files, tmp_path):
    prepared = prepare(_case(case_files), "typesafe/jev-1.13", mode="simple")
    assert prepared.items == [] and len(prepared.requests) == 1
    body = prepared.requests[0][1]
    assert list(body["state"]) == ["narrative"]
    assert "Dr. [PERSON_1]'s branch" in body["state"]["narrative"] and "Pine" not in str(body)
    assert sorted(body["questions"]) == [
        "fac_OV", "fac_QR", "fac_TE", "lvl3_e1", "lvl3_e2", "lvl5_e1", "lvl5_e2",
    ]
    level5 = body["questions"]["lvl5_e2"]["instructions"]
    assert level5["standard"] == [
        "Advances more than two research projects to key milestones.",
        "Introduces novel research approaches beyond routine expectations.",
    ]
    assert "Level 5 (" not in str(body)  # proposed ratings are not sent
    assert any("does not count reliably" in w for w in prepared.warnings)

    supervisor = tmp_path / "supervisor.md"
    supervisor.write_text("The employee led three projects.\n", encoding="utf-8")
    body = prepare(_case(case_files, supervisor=supervisor), "m", mode="simple").requests[0][1]
    assert body["state"]["rating_official_narrative"] == "The employee led three projects.\n"
    assert "`rating_official_narrative`" in body["questions"]["lvl5_e1"]["instructions"]["question"]


def test_simple_decision(case_files):
    def rule(key, text):
        return {"lvl5_e1": 0.9, "lvl5_e2": 0.3, "lvl3_e2": 0.2, "fac_QR": 0.4}.get(key)

    result = run(prepare(_case(case_files), "m", mode="simple"), FakeJev(rule=rule))
    d = result.decision
    assert d.mode == "simple"
    assert [(e.supported, e.verdict) for e in d.elements] == [(5, SUPPORTED), (3, NOT_SUPPORTED)]
    assert d.elements[1].probability == {5: 0.3, 3: 0.2}
    assert d.verdict == NOT_SUPPORTED and d.level5_evidence == pytest.approx(0.6)
    assert [f.probability for f in d.factors] == [0.9, 0.4, 0.9]
    assert any("Jev gives 0.20 that the narrative meets the Level 3 standard" in w for w in result.warnings)
    assert any("does not clearly show Quality & Reliability" in w for w in result.warnings)
    assert result.audit["mode"] == "simple" and result.audit["requests"] == 1


def test_simple_under_rated_and_borderline(case_files):
    ratings = (("Customer Experience", 3), ("Scientific Research", 5))
    result = run(prepare(_case(case_files, ratings=ratings), "m", mode="simple"),
                 FakeJev(rule=lambda key, text: 0.55 if key == "lvl5_e2" else None))
    cx, research = result.decision.elements
    assert cx.verdict == UNDER_RATED and not cx.borderline
    assert research.supported == 5 and research.borderline
