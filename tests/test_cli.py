import csv
import json

import pytest
from click.testing import CliRunner

from conftest import FakeJev
from pmap_calibrate import cli
from pmap_calibrate.config import DEFAULT_MODEL, MissingKeyError, load_settings

RATINGS = ["-r", "Customer Experience=5", "-r", "Scientific Research=5"]


@pytest.fixture
def fake(monkeypatch):
    holder = {"client": FakeJev(p=0.9, count="two"), "key": None}

    def make_client(api_key, model, zdr):
        holder["key"] = api_key
        return holder["client"]

    monkeypatch.setattr(cli, "make_client", make_client)
    return holder


def _invoke(*args):
    return CliRunner().invoke(cli.main, [str(a) for a in args])


def test_dry_run_needs_no_key_and_shows_pseudonymized_items(case_files, tmp_path):
    saved = tmp_path / "requests.jsonl"
    result = _invoke("assess", case_files["narrative"], "--plan", case_files["plan"], *RATINGS,
                     "--names", case_files["names"], "--dry-run", "--save-requests", saved)
    assert result.exit_code == 0, result.output
    assert "Would send 5 request(s)" in result.output
    assert "#0: Served as analytic advisor to Dr. [PERSON_1]'s branch" in result.output
    assert "Pine" not in result.stdout
    rows = [json.loads(line) for line in saved.read_text().splitlines()]
    assert len(rows) == 5 and rows[0]["body"]["model"] == DEFAULT_MODEL


def test_assess_writes_reports(case_files, tmp_path, fake, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    out, js = tmp_path / "report.md", tmp_path / "report.json"
    result = _invoke("assess", case_files["narrative"], "--plan", case_files["plan"], *RATINGS,
                     "--names", case_files["names"], "--out", out, "--json", js)
    assert result.exit_code == 0, result.output
    assert fake["key"] == "sk-test"
    assert "Supported: proposed 5.00 (Level 5, Outstanding)" in result.output
    report = out.read_text(encoding="utf-8")
    assert "| Scientific Research and Innovation | 5 (Outstanding) | 5 (Outstanding) | Supported | 2 of 2 (needs 1) |" in report
    assert "8 instance(s) found; needs more than 2" in report
    assert "Dr. Pine's branch" in report  # reports show the original text
    data = json.loads(js.read_text(encoding="utf-8"))
    assert data["decision"]["supported_summary"] == {"score": 5.0, "level": 5}
    assert data["answers"]["0"]["fac_TE"] == {"type": "noul", "noul": 0.9}
    assert data["audit"]["privacy"]["zdr"] is True


def test_threshold_option_changes_the_outcome(case_files, fake, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    result = _invoke("assess", case_files["narrative"], "--plan", case_files["plan"], *RATINGS, "--threshold", "0.95")
    assert result.exit_code == 0, result.output
    assert "Not supported" in result.output


def test_missing_key_is_a_clear_error(case_files):
    result = _invoke("assess", case_files["narrative"], "--plan", case_files["plan"], *RATINGS)
    assert result.exit_code == 1
    assert "No OpenRouter API key found" in result.output


def test_env_file_option(case_files, tmp_path, fake):
    env = tmp_path / "secrets.env"
    env.write_text("OPENROUTER_API_KEY=sk-from-file\n", encoding="utf-8")
    env.chmod(0o600)
    result = _invoke("--env-file", env, "assess", case_files["narrative"], "--plan", case_files["plan"], *RATINGS)
    assert result.exit_code == 0, result.output
    assert fake["key"] == "sk-from-file"


def test_load_settings_precedence(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("OPENROUTER_API_KEY=sk-dotenv\nPMAP_MODEL=other/model\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    settings = load_settings()
    assert (settings.api_key, settings.model) == ("sk-dotenv", "other/model")
    assert settings.key_source.endswith(".env")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-env")
    assert load_settings().api_key == "sk-env"
    assert load_settings(model="cli/model").model == "cli/model"


def test_load_settings_defaults_and_empty_value(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("OPENROUTER_API_KEY=\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    settings = load_settings()
    assert settings.model == DEFAULT_MODEL == "typesafe/jev-1.13"
    with pytest.raises(MissingKeyError):
        settings.require_key()


def test_show_plan(case_files):
    result = _invoke("show-plan", case_files["plan"], "-r", "Research=5")
    assert result.exit_code == 0, result.output
    assert "Scientific Research and Innovation" in result.output
    assert "[counted: more than 2]" in result.output
    assert "proposed 5 (Outstanding)" in result.output
    assert _invoke("show-plan", case_files["plan"], "-r", "Basketry=5").exit_code == 1


def test_batch(case_files, tmp_path, fake, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    manifest = tmp_path / "manifest.csv"
    manifest.write_text(
        "employee_id,narrative,plan,ratings\n"
        "E1,narrative.md,plan.md,Customer Experience=5; Scientific Research=5\n"
        "E2,narrative.md,plan.md,Customer Experience=3; Scientific Research=3\n"
        "E3,missing.md,plan.md,Customer Experience=5\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "reports"
    result = _invoke("batch", manifest, "--out-dir", out_dir, "--names", case_files["names"])
    assert result.exit_code == 1  # E3 fails; the others still run
    rows = {row["employee_id"]: row for row in csv.DictReader((out_dir / "summary.csv").open())}
    assert rows["E1"]["verdict"] == "Supported"
    assert rows["E2"]["verdict"] == "Possibly under-rated"
    assert rows["E3"]["verdict"] == "Error"
    assert rows["E1"]["level5_evidence"] == "1.00"
    assert (out_dir / "E1.md").exists() and (out_dir / "E2.json").exists()
    assert "Level 5 summaries: 1 proposed, 2 supported" in result.output


def test_names_command(case_files):
    result = _invoke("names", case_files["narrative"])
    assert result.exit_code == 0
    assert result.stdout.split("\n")[:2] == ["Pine", "Zoë Lakey"]


def test_check_model(fake, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    result = _invoke("check-model")
    assert result.exit_code == 0, result.output
    assert "OK" in result.output and "typesafe/jev-1.13-20260917" in result.output
    assert "found in environment variable OPENROUTER_API_KEY" in result.output


def test_simple_mode(case_files, tmp_path, fake, monkeypatch):
    dry = _invoke("assess", case_files["narrative"], "--plan", case_files["plan"], *RATINGS,
                  "--mode", "simple", "--names", case_files["names"], "--dry-run")
    assert dry.exit_code == 0, dry.output
    assert "Would send 1 request(s) with 7 questions" in dry.output
    assert "----- narrative -----" in dry.output and "Pine" not in dry.stdout

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    out = tmp_path / "simple.md"
    result = _invoke("assess", case_files["narrative"], "--plan", case_files["plan"], *RATINGS,
                     "--mode", "simple", "--out", out)
    assert result.exit_code == 0, result.output
    report = out.read_text(encoding="utf-8")
    assert "| Element | Proposed | Supported | Verdict | P(Level 5 met) · P(Level 3 met) |" in report
    assert "| HHS Customer Experience (CX) | 5 (Outstanding) | 5 (Outstanding) | Supported | 0.90 · 0.90 |" in report
    assert "**Level 5 standard as a whole: 0.90 (met)**" in report
    assert "## Narrative items" not in report
