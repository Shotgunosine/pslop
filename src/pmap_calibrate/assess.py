"""Prepare one employee's case, send it to Jev, and decide locally.

Detailed mode sends each narrative item on its own; simple mode sends the whole narrative once.
"""

import csv
import difflib
import re
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .decide import Decision, Rule, decide, decide_simple
from .jev import STATE_TOKEN_LIMIT, request_bodies
from .loaders import load_text, plain_text, sha256
from .narrative import Item, split_items
from .plan import Element, parse_plan
from .pseudonymize import Pseudonymizer, read_names_file
from .questions import QUESTION_VERSION, estimate_tokens, item_questions, narrative_questions
from .ratings import NARRATIVE_LIMIT, NEAR_LIMIT_FRACTION, parse_ratings_field


@dataclass
class EmployeeCase:
    narrative_path: Path
    plan_path: Path
    ratings: list[tuple[str, int]]
    employee_id: str | None = None
    supervisor_path: Path | None = None
    names_paths: list[Path] = field(default_factory=list)
    employee_name: str | None = None
    bargaining_unit: bool = False

    @property
    def label(self) -> str:
        return self.employee_id or self.narrative_path.stem


MODES = ("detailed", "simple")


@dataclass
class PreparedCase:
    case: EmployeeCase
    mode: str
    elements: list[Element]
    items: list[Item]  # empty in simple mode
    proposed: dict[str, int]
    requests: list[tuple[int, dict]]  # (item index, request body); index 0 in simple mode
    pseudonymizer: Pseudonymizer
    narrative_chars: int
    inputs: dict
    warnings: list[str]

    @property
    def question_count(self) -> int:
        return sum(len(body["questions"]) for _, body in self.requests)

    @property
    def estimated_tokens(self) -> int:
        return sum(estimate_tokens(body) for _, body in self.requests)


@dataclass
class CaseResult:
    label: str
    decision: Decision
    items: list[Item]
    answers: dict[int, dict[str, dict]]
    warnings: list[str]
    audit: dict

    def to_dict(self) -> dict:
        data = asdict(self)
        for item in data["items"]:
            item["tags"] = sorted(item["tags"])
        return data


def prepare(case: EmployeeCase, model: str, zdr: bool = True, mode: str = "detailed") -> PreparedCase:
    if mode not in MODES:
        raise ValueError(f"Unknown mode {mode!r}; use one of {', '.join(MODES)}.")
    if not case.ratings:
        raise ValueError("At least one proposed element rating is required (ELEMENT=LEVEL).")
    warnings: list[str] = []
    narrative = load_text(case.narrative_path)
    supervisor = load_text(case.supervisor_path) if case.supervisor_path else None

    elements = parse_plan(load_text(case.plan_path))
    if not elements:
        raise ValueError(
            f"No elements with 'Level 3'/'Level 5' standards found in {case.plan_path}. "
            "Run `pmap-calibrate show-plan` on it to see how it is read."
        )
    for element in elements:
        if not element.at_level(5):
            warnings.append(f"No Level 5 standards found for {element.name!r}; it is not assessed.")

    proposed, unmatched = match_ratings(case.ratings, elements)
    if unmatched:
        names = "; ".join(e.name for e in elements)
        raise ValueError(f"Could not match {', '.join(map(repr, unmatched))} to a plan element. Elements: {names}")
    for element in elements:
        if element.id not in proposed:
            warnings.append(f"No proposed rating for {element.name!r}; it is left out of the proposed summary.")

    chars = len(plain_text(narrative, case.narrative_path).strip())
    if chars > NARRATIVE_LIMIT:
        warnings.append(f"Narrative is {chars} characters, over the {NARRATIVE_LIMIT}-character limit.")
    elif chars >= NARRATIVE_LIMIT * NEAR_LIMIT_FRACTION:
        warnings.append(
            f"Narrative is {chars} characters, within 2% of the {NARRATIVE_LIMIT}-character limit; "
            "confirm the count in the HHS form."
        )

    names = [name for path in case.names_paths for name in read_names_file(path)]
    pseudonymizer = Pseudonymizer(names, case.employee_name)
    sent_elements = [
        replace(e, standards=[replace(s, text=pseudonymizer.apply(s.text)) for s in e.standards])
        for e in elements
    ]

    if mode == "simple":
        items = []
        state = {"narrative": pseudonymizer.apply(narrative)}
        if supervisor:
            state["rating_official_narrative"] = pseudonymizer.apply(supervisor)
        questions = narrative_questions(sent_elements, with_supervisor=bool(supervisor))
        longest = max(estimate_tokens(q) for q in questions.values())
        if estimate_tokens(state) + longest > STATE_TOKEN_LIMIT:
            raise ValueError("The narrative is too long for Jev in simple mode; use --mode detailed.")
        requests = [(0, body) for body in request_bodies(state, questions, model, zdr)]
        if any(s.threshold for e in elements for s in e.standards):
            warnings.append(
                "Simple mode leaves counted standards (such as 'more than two projects') to Jev, which "
                "TypeSafe says does not count reliably; check them by hand or use --mode detailed."
            )
    else:
        items, skipped = split_items(narrative)
        if not items:
            raise ValueError(f"No accomplishment items found in {case.narrative_path}.")
        if skipped:
            warnings.append(f"{skipped} paragraph(s) outside the bulleted list were not assessed.")
        if supervisor:
            items += split_items(supervisor, source="rating official", start=len(items))[0]
        requests = []
        for item in items:
            questions = item_questions(sent_elements, include_flags=item.source == "narrative")
            state = {"accomplishment": pseudonymizer.apply(item.content)}
            requests += [(item.index, body) for body in request_bodies(state, questions, model, zdr)]

    inputs = {"narrative": _file_info(case.narrative_path), "plan": _file_info(case.plan_path)}
    if case.supervisor_path:
        inputs["rating_official_narrative"] = _file_info(case.supervisor_path)
    return PreparedCase(case, mode, elements, items, proposed, requests, pseudonymizer, chars, inputs, warnings)


def run(prepared: PreparedCase, client, rule: Rule = Rule()) -> CaseResult:
    responses = client.decide_all([body for _, body in prepared.requests])
    answers: dict[int, dict] = {}
    for (index, _), response in zip(prepared.requests, responses):
        answers.setdefault(index, {}).update(response.answers)
    if prepared.mode == "simple":
        decision = decide_simple(prepared.elements, answers.get(0, {}), prepared.proposed, rule)
    else:
        decision = decide(prepared.elements, prepared.items, answers, prepared.proposed, rule)
    warnings = list(prepared.warnings) + _result_warnings(decision, prepared.case, rule)

    usage = [r.usage for r in responses]
    p = prepared.pseudonymizer
    audit = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tool_version": __version__,
        "mode": prepared.mode,
        "question_version": QUESTION_VERSION,
        "model_requested": client.model,
        "models_used": sorted({r.model for r in responses}),
        "providers": sorted({r.provider for r in responses if r.provider}),
        "request_ids": [r.id for r in responses],
        "requests": len(responses),
        "questions": prepared.question_count,
        "input_tokens": sum(u.input_tokens for u in usage),
        "cost_usd": round(sum(u.cost or 0.0 for u in usage), 6),
        "rule": asdict(rule),
        "narrative_characters": prepared.narrative_chars,
        "privacy": {
            "pseudonymized": p.active,
            "names_listed": len(p.placeholders),
            "data_collection": "deny",
            "zdr": client.zdr,
            "proposed_ratings_sent": False,
        },
        "inputs": prepared.inputs,
    }
    raw = {index: {key: a.model_dump() for key, a in item.items()} for index, item in answers.items()}
    return CaseResult(prepared.case.label, decision, prepared.items, raw, warnings, audit)


def _result_warnings(decision: Decision, case: EmployeeCase, rule: Rule) -> list[str]:
    warnings = []
    level1_note = ("That alone does not support Level 1, which needs documentation outside the "
                   "narrative, but the rating official may want to confirm.")
    for result in decision.elements:
        if result.supported != 3:
            continue
        if result.probability is not None:
            p3 = result.probability.get(3)
            if p3 is not None and p3 < rule.threshold:
                warnings.append(f"{result.element.name}: Jev gives {p3:.2f} that the narrative meets the "
                                f"Level 3 standard. {level1_note}")
        elif result.level3 and result.level3_met < len(result.level3) / 2:
            warnings.append(f"{result.element.name}: the narrative shows {result.level3_met} of "
                            f"{len(result.level3)} Level 3 standards. {level1_note}")
    note = " (optional for this bargaining unit employee)" if case.bargaining_unit else ""
    for factor in decision.factors:
        if factor.probability is not None:
            if factor.probability < rule.threshold:
                warnings.append(f"The narrative does not clearly show {factor.factor.name}{note}.")
        elif not factor.evidence:
            warnings.append(f"No narrative item clearly shows {factor.factor.name}{note}.")
    if decision.flags.get("directive"):
        warnings.append(
            f"{len(decision.flags['directive'])} item(s) appear to address the reader or evaluator; "
            "read them before relying on the result."
        )
    return warnings


def match_ratings(ratings: list[tuple[str, int]], elements: list[Element]) -> tuple[dict[str, int], list[str]]:
    """Map each proposed rating to a plan element by name; return ({element id: level}, unmatched)."""
    targets = [_norm(e.name) for e in elements]
    titles = [_norm(e.title) for e in elements]
    matched: dict[str, int] = {}
    unmatched = []
    for name, level in ratings:
        key = _norm(name)
        hits = [i for i, (t, full) in enumerate(zip(targets, titles)) if key in (t, full)]
        if not hits:
            hits = [i for i, t in enumerate(titles) if f" {key} " in f" {t} "]
        if len(hits) != 1:
            close = difflib.get_close_matches(key, targets, n=1, cutoff=0.5)
            hits = [targets.index(close[0])] if close else []
        element_id = elements[hits[0]].id if hits else None
        if element_id and element_id not in matched:
            matched[element_id] = level
        else:
            unmatched.append(name)
    return matched, unmatched


def load_manifest(path: Path, shared_names: list[Path] = ()) -> list[EmployeeCase]:
    """Read a batch CSV with columns employee_id, narrative, plan, ratings, and optionally
    names, supervisor, employee_name, bargaining_unit. Paths are relative to the manifest."""
    base = path.parent
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        missing = {"employee_id", "narrative", "plan", "ratings"} - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Manifest {path} is missing column(s): {', '.join(sorted(missing))}")
        cases = []
        for line, row in enumerate(reader, start=2):
            row = {key: (value or "").strip() for key, value in row.items() if key}
            if not any(row.values()):
                continue
            try:
                ratings = parse_ratings_field(row["ratings"])
            except ValueError as err:
                raise ValueError(f"{path}, line {line}: {err}") from None
            names = [base / part.strip() for part in row.get("names", "").split(";") if part.strip()]
            cases.append(EmployeeCase(
                employee_id=row["employee_id"],
                narrative_path=base / row["narrative"],
                plan_path=base / row["plan"],
                ratings=ratings,
                supervisor_path=base / row["supervisor"] if row.get("supervisor") else None,
                names_paths=[*shared_names, *names],
                employee_name=row.get("employee_name") or None,
                bargaining_unit=row.get("bargaining_unit", "").casefold() in {"1", "true", "yes", "y"},
            ))
    duplicates = [label for label, n in Counter(c.label for c in cases).items() if n > 1]
    if duplicates:
        raise ValueError(f"Duplicate employee_id in manifest: {', '.join(duplicates)}")
    return cases


def _file_info(path: Path) -> dict:
    return {"path": str(path), "sha256": sha256(path)}


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", unicodedata.normalize("NFKC", text).casefold()).split())
