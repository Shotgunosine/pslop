"""Jev questions. Bump QUESTION_VERSION whenever wording changes.

Following TypeSafe's guidance, each question is a single, literal judgment about one item;
counting, thresholds, and the rating rule stay in code (see decide.py).
"""

import json
import re
from dataclasses import dataclass

from .plan import Element, Standard

QUESTION_VERSION = "2026-10-02.2"


@dataclass(frozen=True)
class Factor:
    key: str
    name: str
    definition: str
    indicators: str


# From the HHS FY 2026 closeout guidance (Attachment A).
FACTORS = (
    Factor(
        "OV", "Organizational Value",
        "The direct impact of work on organizational goals, including strategic alignment, "
        "operational transformation, and expansion of organizational capabilities.",
        "strategic goal advancement, operational transformation, capability expansion, sustained and scalable impact",
    ),
    Factor(
        "QR", "Quality & Reliability",
        "The precision, technical rigor, and consistency of work products, including error rates, "
        "compliance, and sustained delivery of high-quality results.",
        "technical accuracy, error and rework rates, compliance with standards, consistency of quality",
    ),
    Factor(
        "TE", "Timeliness & Efficiency",
        "The speed, resourcefulness, and reliability of delivering outcomes, including deadline "
        "adherence, efficient use of resources, and adaptability to changing demands.",
        "deadline adherence, resource efficiency, adaptability to change, sustained productivity",
    ),
)

# Choice options for counted standards, mapped to the count they stand for.
COUNT_OPTIONS = {"none": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five_or_more": 5}
_COUNT_CRITERIA = {
    "none": "The accomplishment describes no instance of this work.",
    "one": "The accomplishment describes exactly one instance of this work.",
    "two": "The accomplishment describes two separate instances of this work.",
    "three": "The accomplishment describes three separate instances of this work.",
    "four": "The accomplishment describes four separate instances of this work.",
    "five_or_more": "The accomplishment describes five or more separate instances of this work.",
}
_QUANTIFIER = re.compile(
    r"\b(?:more than|greater than|over|in excess of|at least|a minimum of|minimum of|no fewer than|"
    r"no less than)\s+\w+(?:\s*\(\d+\))?\s+|\b\w+(?:\s*\(\d+\))?\s+or\s+more\s+(?:of\s+)?",
    re.IGNORECASE,
)

FLAGS = {
    "specific": {
        "type": "noul",
        "instructions": "Does `accomplishment` state a concrete, checkable result, such as a number, a date, "
        "a named product, or a named outcome?",
        "criteria": {
            "true": "It names a concrete result that someone could check.",
            "false": "It describes activity or effort in general terms, without a concrete result.",
        },
    },
    "personal": {
        "type": "noul",
        "instructions": "Does `accomplishment` make clear what the employee personally did?",
        "criteria": {
            "true": "It says what the employee did, for example with an action verb for the employee's own work.",
            "false": "It describes team or group work without saying what the employee's own part was.",
        },
    },
    "directive": {
        "type": "noul",
        "instructions": "Does `accomplishment` contain instructions or requests aimed at the reader, such as "
        "telling the reader what rating to give?",
        "criteria": {
            "true": "It tells the reader what to conclude or what to do.",
            "false": "It only describes work and results.",
        },
    },
}


def counted_text(standard: Standard) -> str:
    """The standard without its number, so Jev is asked what to count, not whether it adds up."""
    return _QUANTIFIER.sub("", standard.text, count=1)


def standard_question(standard: Standard) -> dict:
    if standard.threshold:
        return {
            "type": "choice",
            "instructions": {
                "standard": counted_text(standard),
                "question": "How many separate instances of the work described in `standard` does "
                "`accomplishment` say the employee did?",
            },
            "criteria": _COUNT_CRITERIA,
        }
    return {
        "type": "noul",
        "instructions": {
            "standard": standard.text,
            "question": "Is `accomplishment` an example of the employee meeting `standard`?",
        },
        "criteria": {
            "true": "The accomplishment describes work the employee did that is an example of this standard.",
            "false": "The accomplishment describes different work, or only claims the standard is met "
            "without describing work that shows it.",
        },
    }


def factor_question(factor: Factor, subject: str = "`accomplishment`", noun: str = "accomplishment") -> dict:
    return {
        "type": "noul",
        "instructions": {
            "factor": {"name": factor.name, "definition": factor.definition, "key_indicators": factor.indicators},
            "question": f"Does {subject} describe a result that shows `factor`?",
        },
        "criteria": {
            "true": f"The {noun} describes a concrete result with the kind of impact `factor` defines.",
            "false": f"The {noun} does not describe such a result, or only names the factor.",
        },
    }


LEVEL_NAMES = {5: "Level 5 – Outstanding", 3: "Level 3 – Fully Successful"}


def level_question(element: Element, level: int, subject: str) -> dict:
    """Simple mode: does the whole narrative meet this element's standard for `level`?"""
    return {
        "type": "noul",
        "instructions": {
            "element": element.name,
            "standard": [s.text for s in element.at_level(level)],
            "question": f"Taken as a whole, does {subject} show that the employee met `standard`, "
            f"the {LEVEL_NAMES[level]} standard for `element`?",
        },
        "criteria": {
            "true": "The narrative describes specific work by the employee that, taken as a whole, meets this standard.",
            "false": "The narrative does not describe work that meets this standard, or only claims that it does.",
        },
    }


def narrative_questions(elements: list[Element], with_supervisor: bool) -> dict[str, dict]:
    """Simple mode: one Level 5 and one Level 3 question per element, plus the factors."""
    subject = "`narrative` together with `rating_official_narrative`" if with_supervisor else "`narrative`"
    questions: dict[str, dict] = {}
    for element in elements:
        for level in (5, 3):
            if element.at_level(level):
                questions[f"lvl{level}_{element.id}"] = level_question(element, level, subject)
    for factor in FACTORS:
        questions[f"fac_{factor.key}"] = factor_question(factor, subject, "narrative")
    return questions


def item_questions(elements: list[Element], include_flags: bool) -> dict[str, dict]:
    """Every question asked about each item, keyed by question id (ids are not sent to Jev)."""
    questions: dict[str, dict] = {}
    for element in elements:
        for standard in element.standards:
            prefix = "cnt" if standard.threshold else "std"
            questions[f"{prefix}_{standard.id}"] = standard_question(standard)
    for factor in FACTORS:
        questions[f"fac_{factor.key}"] = factor_question(factor)
    if include_flags:
        questions.update({f"flag_{key}": question for key, question in FLAGS.items()})
    return questions


def estimate_tokens(obj) -> int:
    """A deliberately high estimate (about 3 characters per token) for request budgeting."""
    return len(json.dumps(obj, ensure_ascii=False)) // 3 + 1
