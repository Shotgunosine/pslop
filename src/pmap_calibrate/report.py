"""Render results to the console, Markdown, JSON, and a batch summary CSV."""

import csv
import json
from pathlib import Path

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from .assess import CaseResult, PreparedCase
from .decide import NOT_SUPPORTED, SUPPORTED, UNDER_RATED, ElementResult, Rule, StandardResult
from .jev import PRICE_PER_MILLION_INPUT_TOKENS
from .plan import Element
from .ratings import LEVEL_NAMES, level_5_note

ADVISORY = (
    "Advisory. Jev, a decision model, returned probabilities, and the rule above turned them into "
    "levels in code. The calibration board makes the final determination."
)
_STYLE = {SUPPORTED: "green", NOT_SUPPORTED: "red", UNDER_RATED: "yellow"}
_FLAG_LABELS = {
    "vague": "No concrete, checkable result",
    "unclear_attribution": "Unclear what the employee personally did",
    "directive": "Addresses the reader or evaluator",
}


def _level(level: int | None) -> str:
    return "—" if level is None else f"{level} ({LEVEL_NAMES[level]})"


def _summary(result: CaseResult) -> str:
    d = result.decision
    supported = d.supported_summary.describe() if d.supported_summary else "not assessed"
    border = " (borderline)" if d.borderline else ""
    return f"{d.verdict}{border}: proposed {d.proposed_summary.describe()}; evidence supports {supported}."


def _l5(r: ElementResult) -> str:
    return f"{r.level5_met} of {len(r.level5)} (needs {r.level5_needed})" if r.level5 else "—"


def _p(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}"


def _evidence(r: ElementResult) -> str:
    if r.probability is not None:
        return f"{_p(r.probability.get(5))} · {_p(r.probability.get(3))}"
    return _l5(r)


def _evidence_header(mode: str) -> str:
    return "P(Level 5 met) · P(Level 3 met)" if mode == "simple" else "Level 5 standards met"


def rule_text(rule: Rule, mode: str = "detailed") -> str:
    if mode == "simple":
        return (
            f"Jev judged the whole narrative against each element's Level 5 and Level 3 standards. "
            f"Level 5 is supported when P(Level 5 met) ≥ {rule.threshold:.2f}; otherwise Level 3. "
            f"Borderline means P(Level 5 met) is within {rule.margin:.2f} of the threshold."
        )
    return (
        f"A standard is met when at least one narrative item is an example of it with probability "
        f"≥ {rule.threshold:.2f}; for a counted standard, when the instances in the employee's items add "
        f"up past the number. Level 5 needs at least {rule.level5_share:.0%} of the element's Level 5 "
        f"standards met; otherwise Level 3. Borderline means standards within {rule.margin:.2f} of the "
        "threshold (or a count within one) could change the outcome."
    )


def print_result(console: Console, result: CaseResult) -> None:
    d = result.decision
    console.rule(f"[bold]{escape(result.label)}")
    console.print(f"[bold {_STYLE.get(d.verdict, 'white')}]{escape(_summary(result))}[/]")
    table = Table()
    for column in ("Element", "Proposed", "Supported", "Verdict", _evidence_header(d.mode)):
        table.add_column(column)
    for r in d.elements:
        verdict = f"[{_STYLE.get(r.verdict, 'white')}]{r.verdict}[/]" + (" [yellow](borderline)[/]" if r.borderline else "")
        table.add_row(escape(r.element.name), _level(r.proposed), _level(r.supported), verdict, _evidence(r))
    console.print(table)
    console.print(f"[dim]{level_5_note(len(d.elements))}[/]")

    factors = Table(title="Calibration factors", title_justify="left")
    if d.mode == "simple":
        factors.add_column("Factor")
        factors.add_column("P(narrative shows it)")
        for f in d.factors:
            factors.add_row(f.factor.name, _p(f.probability))
    else:
        for column in ("Factor", "Items showing it", "Tagged but not shown"):
            factors.add_column(column)
        own = [i for i in result.items if i.source == "narrative"]
        for f in d.factors:
            factors.add_row(f.factor.name, f"{len(f.evidence)} of {len(own)}", _numbers(f.tagged_unsupported) or "—")
    console.print(factors)

    for key, flagged in d.flags.items():
        if flagged:
            console.print(f"[yellow]{_FLAG_LABELS[key]}:[/] items {_numbers(i for i, _ in flagged)}")
    if result.warnings:
        console.print("[bold yellow]Warnings[/]")
        for warning in result.warnings:
            console.print(f"  • {escape(warning)}")
    a = result.audit
    console.print(
        f"[dim]{a['requests']} request(s), {a['questions']} questions, ${a['cost_usd']:.4f}; "
        f"model {', '.join(a['models_used'])}. {ADVISORY}[/]"
    )


def render_markdown(result: CaseResult) -> str:
    d = result.decision
    items = {i.index: i for i in result.items}
    rule = Rule(**result.audit["rule"])
    lines = [f"# Calibration check: {result.label}", "", f"**{_summary(result)}**", "", f"> {ADVISORY}", ""]
    lines += [
        "## Elements", "",
        f"| Element | Proposed | Supported | Verdict | {_evidence_header(d.mode)} |",
        "|---|---|---|---|---|",
    ]
    for r in d.elements:
        verdict = r.verdict + (" (borderline)" if r.borderline else "")
        lines.append(f"| {_cell(r.element.name)} | {_level(r.proposed)} | {_level(r.supported)} | {verdict} | {_evidence(r)} |")
    lines += ["", f"_{rule_text(rule, d.mode)} {level_5_note(len(d.elements))}_", ""]
    if result.warnings:
        lines += ["## Warnings", "", *(f"- {w}" for w in result.warnings), ""]

    lines += ["## Element details", ""]
    for r in d.elements:
        lines += [f"### {r.element.name}", ""]
        if r.probability is not None:
            for level in (5, 3):
                standards = r.element.at_level(level)
                if standards:
                    p = r.probability.get(level)
                    met = "met" if p is not None and p >= rule.threshold else "not met"
                    lines += [f"**Level {level} standard as a whole: {_p(p)} ({met})**", ""]
                    lines += [f"- {s.text}" for s in standards]
                    lines.append("")
            continue
        if r.level5:
            lines += [f"**Level 5 standards: {_l5(r)}**", ""]
            lines += [line for s in r.level5 for line in _standard_lines(s, items, rule)]
            lines.append("")
        if r.level3:
            lines += [f"**Level 3 standards: {r.level3_met} of {len(r.level3)} met**", ""]
            lines += [line for s in r.level3 for line in _standard_lines(s, items, rule)]
            lines.append("")

    own = [i for i in result.items if i.source == "narrative"]
    if d.mode == "simple":
        lines += ["## Calibration factors", "", "| Factor | P(narrative shows it) |", "|---|---|"]
        lines += [f"| {f.factor.name} | {_p(f.probability)} |" for f in d.factors]
    else:
        lines += ["## Calibration factors", "", "| Factor | Items showing it | Tagged by employee but not shown |", "|---|---|---|"]
        for f in d.factors:
            shown = f"{len(f.evidence)} of {len(own)}: {_numbers(i for i, _ in f.evidence)}" if f.evidence else f"0 of {len(own)}"
            lines.append(f"| {f.factor.name} | {shown} | {_numbers(f.tagged_unsupported) or '—'} |")
    lines.append("")

    if any(d.flags.values()):
        lines += ["## Items to check", ""]
        for key, flagged in d.flags.items():
            if flagged:
                lines.append(f"- **{_FLAG_LABELS[key]}:** {_numbers(i for i, _ in flagged)}")
        lines.append("")

    if result.items:
        lines += ["## Narrative items", "", "Probability that each item shows each calibration factor.", ""]
    for item in result.items:
        answers = result.answers.get(item.index, {})
        probs = " · ".join(
            f"{f.factor.key} {answers[f'fac_{f.factor.key}']['noul']:.2f}"
            for f in d.factors if f"fac_{f.factor.key}" in answers
        )
        source = " (rating official)" if item.source != "narrative" else ""
        lines.append(f"- **#{item.index}**{source} {item.text}  ")
        lines.append(f"  {probs}")
    lines += ["", "## Audit", "", "```json", json.dumps(result.audit, indent=2), "```", ""]
    return "\n".join(lines)


def _standard_lines(s: StandardResult, items: dict, rule: Rule) -> list[str]:
    mark = "Met" if s.met else "Not met"
    flag = ", borderline" if s.uncertain else ""
    if s.count is not None:
        detail = f"{s.count} instance(s) found; needs {s.standard.threshold.describe()}"
    else:
        detail = f"best item {s.best:.2f}"
    lines = [f"- **{mark}**{flag} ({detail}): {s.standard.text}"]
    if s.count is not None:
        for index, count in s.counts[:6]:
            lines.append(f"  - #{index} ({count}): {_short(items[index].text)}")
    else:
        for index, p in s.evidence[:3]:
            lines.append(f"  - #{index} ({p:.2f}): {_short(items[index].text)}")
    return lines


def _short(text: str, limit: int = 160) -> str:
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + " …"


def _numbers(indices) -> str:
    return ", ".join(f"#{i}" for i in indices)


def _cell(text: str) -> str:
    return text.replace("|", "\\|")


def render_json(result: CaseResult) -> str:
    return json.dumps(result.to_dict(), indent=2, ensure_ascii=False, default=str)


def describe_request(prepared: PreparedCase, model: str) -> str:
    tokens = prepared.estimated_tokens
    lines = [
        f"Would send {len(prepared.requests)} request(s) with {prepared.question_count} questions to {model} "
        f"(about {tokens:,} input tokens, roughly ${tokens * PRICE_PER_MILLION_INPUT_TOKENS / 1e6:.4f}).",
    ]
    p = prepared.pseudonymizer
    if prepared.mode == "simple":
        lines += ["Proposed ratings are not sent. The state is the whole narrative:", ""]
        for key, value in prepared.requests[0][1]["state"].items():
            lines += [f"----- {key} -----", value, ""]
        return "\n".join(lines)
    lines += ["Proposed ratings are not sent. Each request's state is one narrative item:", ""]
    for item in prepared.items:
        source = " (rating official)" if item.source != "narrative" else ""
        lines.append(f"#{item.index}{source}: {p.apply(item.content)}")
    return "\n".join(lines)


def print_plan(console: Console, elements: list[Element], proposed: dict[str, int] | None = None) -> None:
    for element in elements:
        rating = f"  [bold]proposed {_level(proposed[element.id])}[/]" if proposed and element.id in proposed else ""
        console.print(f"[bold]{escape(element.name)}[/] [dim]({escape(element.title)})[/]{rating}")
        for s in element.standards:
            threshold = f"[cyan]{escape(f'[counted: {s.threshold.describe()}]')}[/] " if s.threshold else ""
            console.print(f"  L{s.level} {threshold}{escape(s.text)}")
        console.print()


SUMMARY_COLUMNS = [
    "employee_id", "verdict", "borderline", "proposed_score", "proposed_level", "supported_score",
    "supported_level", "level5_evidence", "elements_not_supported", "elements_possibly_under_rated",
    "elements_borderline", "warnings", "models_used", "cost_usd", "report", "error",
]


def write_batch_summary(out_dir: Path, results: list[CaseResult], errors: dict[str, str], reports: dict[str, Path]) -> Path:
    path = out_dir / "summary.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SUMMARY_COLUMNS)
        writer.writeheader()
        for r in results:
            d = r.decision
            writer.writerow({
                "employee_id": r.label,
                "verdict": d.verdict,
                "borderline": "yes" if d.borderline else "",
                "proposed_score": f"{d.proposed_summary.score:.2f}",
                "proposed_level": d.proposed_summary.level,
                "supported_score": f"{d.supported_summary.score:.2f}" if d.supported_summary else "",
                "supported_level": d.supported_summary.level if d.supported_summary else "",
                "level5_evidence": f"{d.level5_evidence:.2f}",
                "elements_not_supported": "; ".join(e.element.name for e in d.elements if e.verdict == NOT_SUPPORTED),
                "elements_possibly_under_rated": "; ".join(e.element.name for e in d.elements if e.verdict == UNDER_RATED),
                "elements_borderline": "; ".join(e.element.name for e in d.elements if e.borderline),
                "warnings": " | ".join(r.warnings),
                "models_used": "; ".join(r.audit["models_used"]),
                "cost_usd": f"{r.audit['cost_usd']:.6f}",
                "report": str(reports.get(r.label, "")),
                "error": "",
            })
        for label, error in errors.items():
            writer.writerow({"employee_id": label, "verdict": "Error", "error": error})
    return path


def print_batch_summary(console: Console, results: list[CaseResult], errors: dict[str, str]) -> None:
    table = Table(title="Batch summary (strongest Level 5 evidence first)", title_justify="left")
    for column in ("Employee", "Proposed", "Supported", "Verdict", "Level 5 evidence"):
        table.add_column(column)
    for r in sorted(results, key=lambda r: -r.decision.level5_evidence):
        d = r.decision
        verdict = f"[{_STYLE.get(d.verdict, 'white')}]{d.verdict}[/]" + (" [yellow](borderline)[/]" if d.borderline else "")
        supported = d.supported_summary.describe() if d.supported_summary else "—"
        table.add_row(escape(r.label), d.proposed_summary.describe(), supported, verdict, f"{d.level5_evidence:.2f}")
    for label, error in errors.items():
        table.add_row(escape(label), "—", "—", f"[red]Error: {escape(error)}[/]", "—")
    console.print(table)
    proposed_5 = sum(r.decision.proposed_summary.level == 5 for r in results)
    supported_5 = sum(bool(r.decision.supported_summary) and r.decision.supported_summary.level == 5 for r in results)
    console.print(
        f"Level 5 summaries: {proposed_5} proposed, {supported_5} supported by the evidence "
        f"(of {len(results)} assessed). HHS caps the number of Level 5 ratings."
    )
