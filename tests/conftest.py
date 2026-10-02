from pathlib import Path

import pytest

from pmap_calibrate.jev import DecisionsResponse

REPO = Path(__file__).resolve().parent.parent

PLAN = """\
### Required - HHS Customer Experience (CX)

Customers are internal and external.

**Level 3 – Fully Successful (FS)**

Responds to each customer request within two business days.

**Level 5 – Outstanding (O)**

Routinely responds to customer inquiries within one business day. Serves as a trusted advisor to stakeholders.

### New Critical Element #1: Scientific Research and Innovation

**Level 3 – Fully Successful (FS)**

* Carries out research projects of these types:
  * new data analysis methods
  * support projects

**Level 5 – Outstanding (O)**

* Advances more than two research projects to key milestones.
* Introduces novel research approaches beyond routine expectations.
"""

NARRATIVE = """\
Calibration factor key: OV = Organizational Value; QR = Quality & Reliability; TE = Timeliness & Efficiency.

## Customer Experience

* Served as analytic advisor to Dr. Pine's branch, always responding within one business day. \\[TE\\]

## Scientific Research and Innovation

* Advanced three projects to key milestones with Zoë Lakey. \\[QR\\]
  * Resting-state project completed data collection.
  * Cognition project finished preregistered analyses.
* Developed a new network-mapping method now used by two labs. \\[OV\\]
"""


def answer_for(question: dict, value):
    if question["type"] == "noul":
        return {"type": "noul", "noul": value}
    options = list(question["criteria"])
    return {
        "type": "choice", "choice": value, "confidence": 0.9,
        "probabilities": {o: (0.9 if o == value else 0.1 / (len(options) - 1)) for o in options},
    }


class FakeJev:
    """Stands in for JevClient. `rule(key, text)` returns a noul probability or a choice option,
    or None for the defaults (noul `p`, choice `count`)."""

    model = "typesafe/jev-1.13"
    zdr = True

    def __init__(self, p=0.9, count="one", rule=None):
        self.p, self.count, self.rule = p, count, rule or (lambda key, text: None)
        self.bodies = []

    def decide_all(self, bodies):
        self.bodies += bodies
        return [self._respond(body) for body in bodies]

    def _respond(self, body):
        state = body["state"]
        text = state.get("accomplishment") or "\n".join(v for k, v in state.items())
        answers = {}
        for key, question in body["questions"].items():
            value = self.rule(key, text)
            if value is None:
                value = self.p if question["type"] == "noul" else self.count
            answers[key] = answer_for(question, value)
        return DecisionsResponse.model_validate({
            "id": "gen-dec-test", "model": "typesafe/jev-1.13-20260917", "provider": "TypeSafe",
            "answers": answers, "usage": {"input_tokens": 100, "output_tokens": 5, "cost": 0.0001},
        })


@pytest.fixture
def case_files(tmp_path):
    plan = tmp_path / "plan.md"
    plan.write_text(PLAN, encoding="utf-8")
    narrative = tmp_path / "narrative.md"
    narrative.write_text(NARRATIVE, encoding="utf-8")
    names = tmp_path / "names.txt"
    names.write_text("# people\nDaniel Pine\nZoë Lakey\n", encoding="utf-8")
    return {"plan": plan, "narrative": narrative, "names": names}


@pytest.fixture(autouse=True)
def no_real_key(monkeypatch, tmp_path_factory):
    """Keep tests from picking up a real key or .env file."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("PMAP_MODEL", raising=False)
    monkeypatch.setattr("pmap_calibrate.config.USER_ENV_FILE", tmp_path_factory.mktemp("cfg") / "env")
    monkeypatch.chdir(tmp_path_factory.mktemp("cwd"))
