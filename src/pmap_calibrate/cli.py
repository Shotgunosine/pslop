"""Command-line interface: assess, batch, show-plan, names, check-model."""

import json
import re
import time
from collections import Counter
from pathlib import Path

import click
from rich.console import Console

from . import __version__
from .assess import MODES, EmployeeCase, PreparedCase, load_manifest, match_ratings, prepare, run
from .config import MissingKeyError, load_settings
from .decide import Rule
from .jev import JevAuthError, JevClient, JevError, request_bodies
from .loaders import load_text
from .plan import Standard, Threshold, parse_plan
from .pseudonymize import suggest_names
from .questions import FACTORS, factor_question, standard_question
from .ratings import parse_rating
from .report import (
    describe_request, print_batch_summary, print_plan, print_result, render_json, render_markdown,
    write_batch_summary,
)

console = Console()
err_console = Console(stderr=True)
EXISTING_FILE = click.Path(exists=True, dir_okay=False, path_type=Path)
PROBABILITY = click.FloatRange(0.0, 1.0)

CHECK_STATE = {
    "accomplishment": "Submitted all 12 monthly program reports a week ahead of the deadline, and redesigned "
    "the team's report template; three colleagues now use it, which cut preparation time in half."
}


def make_client(api_key: str, model: str, zdr: bool) -> JevClient:
    return JevClient(api_key, model, zdr=zdr)


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="pmap-calibrate")
@click.option(
    "--env-file", type=EXISTING_FILE,
    help="Read OPENROUTER_API_KEY (and PMAP_MODEL) from this file instead of ./.env or "
    "~/.config/pmap-calibrate/env. A variable already set in the environment wins.",
)
@click.pass_context
def main(ctx: click.Context, env_file: Path | None) -> None:
    """Check whether proposed HHS performance ratings are supported by the written record.

    Jev, a decision model, returns probabilities instead of text. In the default detailed mode,
    each narrative bullet is sent on its own and Jev judges whether it is an example of each
    standard in the plan; a transparent rule in code then decides the level. In simple mode, Jev
    judges once whether the whole narrative meets each element's Level 5 and Level 3 standards.
    Proposed ratings are never sent. Results are advisory.
    """
    ctx.obj = {"env_file": env_file}


def model_options(command):
    options = [
        click.option("--mode", type=click.Choice(MODES), default="detailed", show_default=True,
                     help="detailed: ask about each narrative item against each standard. simple: ask once "
                     "whether the whole narrative meets each element's Level 5 and Level 3 standards."),
        click.option("--model", help="OpenRouter model ID. Default: $PMAP_MODEL, else typesafe/jev-1.13."),
        click.option("--zdr/--no-zdr", default=True, show_default=True,
                     help="Route only to OpenRouter endpoints with zero data retention."),
        click.option("--threshold", type=PROBABILITY, default=0.5, show_default=True,
                     help="Probability at which Jev's yes counts: an item is an example of a standard "
                     "(detailed) or the narrative meets a level's standard (simple)."),
        click.option("--level5-share", type=PROBABILITY, default=0.5, show_default=True,
                     help="Detailed mode: share of an element's Level 5 standards that must be met for Level 5."),
        click.option("--names", "names_paths", type=EXISTING_FILE, multiple=True,
                     help="Optional names file (one name per line). Listed names are replaced with "
                     "placeholders before anything is sent. Repeatable."),
        click.option("--dry-run", is_flag=True, help="Show what would be sent to Jev, without sending it."),
        click.option("--save-requests", type=click.Path(dir_okay=False, path_type=Path),
                     help="Write every request body sent (or, with --dry-run, that would be sent) as JSON lines."),
    ]
    for option in reversed(options):
        command = option(command)
    return command


@main.command()
@click.argument("narrative", type=EXISTING_FILE)
@click.option("--plan", type=EXISTING_FILE, required=True, help="Performance plan with element standards.")
@click.option("-r", "--rating", "ratings", multiple=True, required=True, metavar="ELEMENT=LEVEL",
              help="Proposed rating for one element, e.g. -r 'Customer Experience=5'. Repeat for each element.")
@click.option("--supervisor", type=EXISTING_FILE, help="Rating official's narrative, used as extra evidence.")
@click.option("--employee-id", help="Label for the report (default: narrative file name).")
@click.option("--employee-name", help="Employee's name, replaced with [EMPLOYEE] before sending.")
@click.option("--bargaining-unit", is_flag=True,
              help="Bargaining unit employee: calibration factors are encouraged, not required.")
@model_options
@click.option("--out", type=click.Path(dir_okay=False, path_type=Path), help="Write a Markdown report here.")
@click.option("--json", "json_out", type=click.Path(dir_okay=False, path_type=Path),
              help="Write the full result, including every raw Jev answer, as JSON here.")
@click.pass_context
def assess(ctx, narrative, plan, ratings, supervisor, employee_id, employee_name, bargaining_unit,
           mode, model, zdr, threshold, level5_share, names_paths, dry_run, save_requests, out, json_out):
    """Assess one employee's NARRATIVE against the plan and proposed element ratings."""
    settings = _settings(ctx, model)
    try:
        case = EmployeeCase(
            narrative_path=narrative, plan_path=plan, ratings=[parse_rating(r) for r in ratings],
            employee_id=employee_id, supervisor_path=supervisor, names_paths=list(names_paths),
            employee_name=employee_name, bargaining_unit=bargaining_unit,
        )
        prepared = prepare(case, settings.model, zdr, mode)
    except (ValueError, OSError) as err:
        raise click.ClickException(str(err)) from err
    _privacy_note(prepared)
    if save_requests:
        _save_requests(save_requests, [prepared])

    if dry_run:
        click.echo(describe_request(prepared, settings.model))
        for warning in prepared.warnings:
            err_console.print(f"[yellow]Warning:[/] {warning}")
        return

    client = _client(settings, zdr)
    try:
        with err_console.status(f"Asking {client.model} ({len(prepared.requests)} requests)…"):
            result = run(prepared, client, Rule(threshold=threshold, level5_share=level5_share))
    except JevError as err:
        raise click.ClickException(str(err)) from err

    print_result(console, result)
    if out:
        out.write_text(render_markdown(result), encoding="utf-8")
        console.print(f"Report: {out}")
    if json_out:
        json_out.write_text(render_json(result), encoding="utf-8")
        console.print(f"JSON: {json_out}")


@main.command()
@click.argument("manifest", type=EXISTING_FILE)
@click.option("--out-dir", type=click.Path(file_okay=False, path_type=Path), default=Path("reports"),
              show_default=True, help="Where to write per-employee reports and summary.csv.")
@model_options
@click.pass_context
def batch(ctx, manifest, out_dir, mode, model, zdr, threshold, level5_share, names_paths, dry_run, save_requests):
    """Assess every employee listed in a MANIFEST CSV.

    Columns: employee_id, narrative, plan, ratings ("Element=5; Element=3"), and optionally
    names (names files, separated by ';'), supervisor, employee_name, bargaining_unit (yes/no).
    Paths are relative to the manifest. --names files apply to every row.
    """
    settings = _settings(ctx, model)
    try:
        cases = load_manifest(manifest, list(names_paths))
    except (ValueError, OSError) as err:
        raise click.ClickException(str(err)) from err
    out_dir.mkdir(parents=True, exist_ok=True)
    client = None if dry_run else _client(settings, zdr)
    rule = Rule(threshold=threshold, level5_share=level5_share)

    results, errors, reports, prepared_cases = [], {}, {}, []
    for number, case in enumerate(cases, start=1):
        prefix = f"[{number}/{len(cases)}] {case.label}"
        stem = _safe(case.label)
        try:
            prepared = prepare(case, settings.model, zdr, mode)
            prepared_cases.append(prepared)
            if dry_run:
                path = out_dir / f"{stem}.request.txt"
                path.write_text(describe_request(prepared, settings.model), encoding="utf-8")
                console.print(f"{prefix}: {len(prepared.requests)} request(s); items written to {path}")
                continue
            with err_console.status(f"{prefix}: asking {client.model} ({len(prepared.requests)} requests)…"):
                result = run(prepared, client, rule)
        except JevAuthError as err:
            raise click.ClickException(str(err)) from err
        except (JevError, ValueError, OSError) as err:
            errors[case.label] = str(err)
            err_console.print(f"[red]{prefix}: {err}[/]")
            continue
        md_path = out_dir / f"{stem}.md"
        md_path.write_text(render_markdown(result), encoding="utf-8")
        (out_dir / f"{stem}.json").write_text(render_json(result), encoding="utf-8")
        reports[case.label] = md_path
        results.append(result)
        console.print(f"{prefix}: {result.decision.verdict}{' (borderline)' if result.decision.borderline else ''}")

    if save_requests:
        _save_requests(save_requests, prepared_cases)
    if dry_run:
        return
    print_batch_summary(console, results, errors)
    console.print(f"Summary: {write_batch_summary(out_dir, results, errors, reports)}")
    if errors:
        raise click.ClickException(f"{len(errors)} of {len(cases)} employee(s) could not be assessed.")


@main.command("show-plan")
@click.argument("plan", type=EXISTING_FILE)
@click.option("-r", "--rating", "ratings", multiple=True, metavar="ELEMENT=LEVEL",
              help="Optionally check that proposed ratings match the plan's elements.")
def show_plan(plan, ratings):
    """Show how PLAN is read: elements, standards, and counted thresholds. Runs locally."""
    try:
        elements = parse_plan(load_text(plan))
        parsed = [parse_rating(r) for r in ratings]
    except (ValueError, OSError) as err:
        raise click.ClickException(str(err)) from err
    if not elements:
        raise click.ClickException(
            "No elements found. Elements need a heading (or a 'Critical Element' line) followed by "
            "'Level 3 – Fully Successful' and 'Level 5 – Outstanding' sections."
        )
    proposed, unmatched = match_ratings(parsed, elements)
    print_plan(console, elements, proposed)
    if unmatched:
        raise click.ClickException(f"Could not match {', '.join(map(repr, unmatched))} to a plan element.")


@main.command()
@click.argument("files", nargs=-1, required=True, type=EXISTING_FILE)
def names(files):
    """List likely personal names in FILES, to start a --names file. Runs locally; nothing is sent.

    Review the list: remove anything that is not a person, and add names it missed.
    Example: pmap-calibrate names narrative.md > names.txt
    """
    counts: Counter[str] = Counter()
    for path in files:
        try:
            text = load_text(path)
        except (ValueError, OSError) as err:
            raise click.ClickException(str(err)) from err
        for name, count in suggest_names(text):
            counts[name] += count
    err_console.print(f"[dim]{len(counts)} likely name(s). Review before use; this is a heuristic.[/]")
    for name in counts:
        click.echo(name)


@main.command("check-model")
@click.option("--model", help="OpenRouter model ID. Default: $PMAP_MODEL, else typesafe/jev-1.13.")
@click.option("--zdr/--no-zdr", default=True, show_default=True,
              help="Route only to OpenRouter endpoints with zero data retention.")
@click.pass_context
def check_model(ctx, model, zdr):
    """Check the API key, routing, and answer types with made-up data (no PII)."""
    settings = _settings(ctx, model)
    console.print(f"API key: {f'found in {settings.key_source}' if settings.api_key else '[red]not found[/]'}")
    console.print(f"Model: {settings.model} (from {settings.model_source})")
    client = _client(settings, zdr)
    questions = {
        "noul": standard_question(Standard("check", 5, "Improves a process used by the team.")),
        "choice": standard_question(Standard("count", 5, "Submits at least ten reports ahead of the deadline.",
                                             Threshold(">=", 10))),
        "factor": factor_question(FACTORS[2]),
    }
    start = time.monotonic()
    try:
        [response] = client.decide_all(request_bodies(CHECK_STATE, questions, settings.model, zdr))
    except JevError as err:
        raise click.ClickException(str(err)) from err
    a = response.answers
    console.print(
        f"[green]OK[/]: {response.model} via {response.provider or 'OpenRouter'} answered in "
        f"{time.monotonic() - start:.1f}s for ${response.usage.cost or 0:.6f}. "
        f"Example of standard: {a['noul'].noul:.2f}; instances counted: {a['choice'].choice}; "
        f"shows Timeliness & Efficiency: {a['factor'].noul:.2f}."
    )


def _settings(ctx: click.Context, model: str | None):
    try:
        settings = load_settings(ctx.obj["env_file"], model)
    except FileNotFoundError as err:
        raise click.ClickException(str(err)) from err
    for warning in settings.warnings:
        err_console.print(f"[yellow]Warning:[/] {warning}")
    return settings


def _client(settings, zdr: bool) -> JevClient:
    try:
        return make_client(settings.require_key(), settings.model, zdr)
    except MissingKeyError as err:
        raise click.ClickException(str(err)) from err


def _privacy_note(prepared: PreparedCase) -> None:
    p = prepared.pseudonymizer
    if p.active:
        err_console.print(
            f"[dim]Pseudonymizing {len(p.placeholders)} listed name(s). Names not in the list are sent unchanged.[/]"
        )
    else:
        err_console.print("[dim]Pseudonymization is off: names are sent as written. Use --names FILE to replace them.[/]")


def _save_requests(path: Path, prepared_cases: list[PreparedCase]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for prepared in prepared_cases:
            for index, body in prepared.requests:
                record = {"employee": prepared.case.label, "item": index, "body": body}
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    err_console.print(f"[dim]Request bodies written to {path}[/]")


def _safe(label: str) -> str:
    return re.sub(r"[^\w.-]+", "_", label).strip("._") or "employee"
