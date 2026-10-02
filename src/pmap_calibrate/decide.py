"""Combine Jev's answers into standard, element, and factor results.

Detailed mode (per narrative item), all in code:
- A standard is met when at least one item is an example of it with probability >= threshold.
  A counted standard ("more than two projects") is met when the instances Jev reads in the
  employee's own items add up past the number. Sub-bullets and their parent are counted once,
  as the larger of the parent's count and the children's total.
- An element is supported at Level 5 when at least `level5_share` of its Level 5 standards are
  met, and at Level 3 otherwise. A narrative alone never establishes Level 1.
- A result is borderline when standards near the cutoff (probability within `margin` of the
  threshold, or a count within one of the number) could flip the outcome.

Simple mode (whole narrative): Jev judges whether the narrative as a whole meets each element's
Level 5 standard and its Level 3 standard. The element is supported at Level 5 when that
probability is >= threshold, and at Level 3 otherwise; it is borderline within `margin`.
"""

import math
from dataclasses import dataclass, field

from .narrative import Item
from .plan import Element, Standard
from .questions import COUNT_OPTIONS, FACTORS, Factor
from .ratings import Summary, summary

SUPPORTED = "Supported"
NOT_SUPPORTED = "Not supported"
UNDER_RATED = "Possibly under-rated"
NO_PROPOSAL = "No proposed rating"
NOT_ASSESSED = "Not assessed"


@dataclass(frozen=True)
class Rule:
    threshold: float = 0.5
    level5_share: float = 0.5
    margin: float = 0.15


@dataclass
class StandardResult:
    standard: Standard
    met: bool
    uncertain: bool
    best: float
    evidence: list[tuple[int, float]]  # (item index, probability), strongest first
    count: int | None = None
    counts: list[tuple[int, int]] = field(default_factory=list)  # (item index, instances) for counted standards


@dataclass
class ElementResult:
    element: Element
    proposed: int | None
    supported: int | None
    verdict: str
    borderline: bool
    level5: list[StandardResult]
    level3: list[StandardResult]
    level5_needed: int
    probability: dict[int, float | None] | None = None  # simple mode: {level: P(standard met)}

    @property
    def level5_met(self) -> int:
        return sum(r.met for r in self.level5)

    @property
    def level3_met(self) -> int:
        return sum(r.met for r in self.level3)


@dataclass
class FactorResult:
    factor: Factor
    evidence: list[tuple[int, float]]
    tagged_unsupported: list[int]  # items the employee tagged with this factor that Jev did not support
    probability: float | None = None  # simple mode: P(the whole narrative shows the factor)


@dataclass
class Decision:
    elements: list[ElementResult]
    factors: list[FactorResult]
    flags: dict[str, list[tuple[int, float]]]
    proposed_summary: Summary
    supported_summary: Summary | None
    verdict: str
    borderline: bool
    # Detailed mode: share of all Level 5 standards met. Simple mode: mean P(Level 5 standard met).
    level5_evidence: float
    mode: str = "detailed"


def verdict(proposed: int | None, supported: int | None) -> str:
    if supported is None:
        return NOT_ASSESSED
    if proposed is None:
        return NO_PROPOSAL
    if proposed == 1:
        return NOT_ASSESSED  # Level 1 rests on documentation outside the narrative.
    if supported == proposed:
        return SUPPORTED
    return NOT_SUPPORTED if supported < proposed else UNDER_RATED


def noul(answers: dict, item: int, key: str) -> float | None:
    answer = answers.get(item, {}).get(key)
    return answer.noul if answer is not None else None


def assess_standard(standard: Standard, items: list[Item], answers: dict, rule: Rule) -> StandardResult:
    if standard.threshold is None:
        probs = [(i.index, p) for i in items if (p := noul(answers, i.index, f"std_{standard.id}")) is not None]
        best = max((p for _, p in probs), default=0.0)
        evidence = sorted(((i, p) for i, p in probs if p >= rule.threshold), key=lambda x: -x[1])
        return StandardResult(standard, best >= rule.threshold, abs(best - rule.threshold) < rule.margin, best, evidence)

    own = [i for i in items if i.source == "narrative"]
    counts, evidence = {}, []
    for item in own:
        answer = answers.get(item.index, {}).get(f"cnt_{standard.id}")
        if answer is None:
            continue
        counts[item.index] = COUNT_OPTIONS.get(answer.choice, 0)
        p_any = 1.0 - answer.probabilities.get("none", 1.0 if answer.choice == "none" else 0.0)
        if counts[item.index] > 0:
            evidence.append((item.index, p_any))
    children: dict[int, list[int]] = {}
    for item in own:
        if item.parent is not None:
            children.setdefault(item.parent, []).append(item.index)

    def group(index: int) -> int:
        return max(counts.get(index, 0), sum(group(child) for child in children.get(index, [])))

    roots = {i.index for i in own} - {c for kids in children.values() for c in kids}
    total = sum(group(root) for root in roots)
    t = standard.threshold
    uncertain = t.met(total) != t.met(total - 1) or t.met(total) != t.met(total + 1)
    return StandardResult(
        standard, t.met(total), uncertain, max((p for _, p in evidence), default=0.0),
        sorted(evidence, key=lambda x: -x[1]), count=total,
        counts=sorted(((i, c) for i, c in counts.items() if c), key=lambda x: x[0]),
    )


def decide(elements: list[Element], items: list[Item], answers: dict, proposed: dict[str, int],
           rule: Rule = Rule()) -> Decision:
    results = []
    for element in elements:
        level5 = [assess_standard(s, items, answers, rule) for s in element.at_level(5)]
        level3 = [assess_standard(s, items, answers, rule) for s in element.at_level(3)]
        needed = math.ceil(rule.level5_share * len(level5)) if level5 else 0
        if level5:
            sure = sum(r.met and not r.uncertain for r in level5)
            possible = sum(r.met or r.uncertain for r in level5)
            supported = 5 if sum(r.met for r in level5) >= needed else 3
            borderline = (sure >= needed) != (possible >= needed)
        else:
            supported, borderline = None, False
        results.append(ElementResult(
            element, proposed.get(element.id), supported, verdict(proposed.get(element.id), supported),
            borderline, level5, level3, needed,
        ))

    own = [i for i in items if i.source == "narrative"]
    factors = []
    for factor in FACTORS:
        probs = {i.index: noul(answers, i.index, f"fac_{factor.key}") or 0.0 for i in own}
        evidence = sorted(((i, p) for i, p in probs.items() if p >= rule.threshold), key=lambda x: -x[1])
        unsupported = [i.index for i in own if factor.key in i.tags and probs[i.index] < rule.threshold]
        factors.append(FactorResult(factor, evidence, unsupported))

    flags = {
        "vague": [(i.index, p) for i in own if (p := noul(answers, i.index, "flag_specific")) is not None and p < rule.threshold],
        "unclear_attribution": [(i.index, p) for i in own if (p := noul(answers, i.index, "flag_personal")) is not None and p < rule.threshold],
        "directive": [(i.index, p) for i in own if (p := noul(answers, i.index, "flag_directive")) is not None and p >= rule.threshold],
    }

    total5 = sum(len(r.level5) for r in results)
    share = sum(r.level5_met for r in results) / total5 if total5 else 0.0
    return _finish(results, factors, flags, proposed, share, "detailed")


def decide_simple(elements: list[Element], answers: dict, proposed: dict[str, int], rule: Rule = Rule()) -> Decision:
    """`answers` holds one whole-narrative request's answers, keyed by question id."""
    def p(key: str) -> float | None:
        return answers[key].noul if key in answers else None

    results = []
    for element in elements:
        p5, p3 = p(f"lvl5_{element.id}"), p(f"lvl3_{element.id}")
        supported = None if p5 is None else 5 if p5 >= rule.threshold else 3
        borderline = p5 is not None and abs(p5 - rule.threshold) < rule.margin
        results.append(ElementResult(
            element, proposed.get(element.id), supported, verdict(proposed.get(element.id), supported),
            borderline, [], [], 0, probability={5: p5, 3: p3},
        ))
    factors = [FactorResult(f, [], [], probability=p(f"fac_{f.key}")) for f in FACTORS]
    level5 = [r.probability[5] for r in results if r.probability[5] is not None]
    evidence = sum(level5) / len(level5) if level5 else 0.0
    return _finish(results, factors, {}, proposed, evidence, "simple")


def _finish(results, factors, flags, proposed, level5_evidence, mode) -> Decision:
    proposed_summary = summary(list(proposed.values()))
    assessed = [r for r in results if r.supported is not None]
    supported_summary = summary([r.supported for r in assessed]) if assessed else None
    if supported_summary:
        low = summary([3 if r.borderline else r.supported for r in assessed]).level
        high = summary([5 if r.borderline else r.supported for r in assessed]).level
        borderline = low != high
        overall = verdict(proposed_summary.level, supported_summary.level)
    else:
        borderline, overall = False, NOT_ASSESSED
    return Decision(results, factors, flags, proposed_summary, supported_summary, overall, borderline,
                    level5_evidence, mode)
