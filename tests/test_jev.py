import json

import httpx
import pytest

from pmap_calibrate import jev
from pmap_calibrate.jev import DECISIONS_URL, JevAuthError, JevClient, JevError, request_bodies

QUESTIONS = {
    "is_example": {"type": "noul", "instructions": "Is `accomplishment` an example?"},
    "count": {"type": "choice", "instructions": "How many?", "criteria": {"none": "None.", "one": "One."}},
}
OK = {
    "id": "gen-dec-1", "model": "typesafe/jev-1.13-20260917", "provider": "TypeSafe",
    "answers": {
        "is_example": {"type": "noul", "noul": 0.81},
        "count": {"type": "choice", "choice": "one", "confidence": 0.7, "probabilities": {"none": 0.2, "one": 0.8}},
    },
    "usage": {"input_tokens": 300, "output_tokens": 20, "cost": 0.0000126},
}


def client_with(*responses, seen=None):
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        status, body = queue.pop(0)
        return httpx.Response(status, json=body, headers={"retry-after": "0"} if status == 429 else {})

    return JevClient("sk-test", "typesafe/jev-1.13", http=httpx.Client(transport=httpx.MockTransport(handler)))


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(jev.time, "sleep", lambda s: None)


def body():
    return request_bodies({"accomplishment": "Did a thing."}, QUESTIONS, "typesafe/jev-1.13", zdr=True)[0]


def test_decide_sends_body_and_parses_answers():
    seen = []
    result = client_with((200, OK), seen=seen).decide(body())
    [request] = seen
    assert str(request.url) == DECISIONS_URL
    assert request.headers["authorization"] == "Bearer sk-test"
    sent = json.loads(request.content)
    assert sent["model"] == "typesafe/jev-1.13"
    assert sent["provider"] == {"data_collection": "deny", "zdr": True}
    assert result.answers["is_example"].noul == 0.81
    assert result.answers["count"].choice == "one"
    assert result.usage.cost == 0.0000126


def test_retries_rate_limit_then_succeeds():
    seen = []
    result = client_with((429, {"error": {"code": 429, "message": "Rate limit exceeded"}}), (200, OK), seen=seen).decide(body())
    assert len(seen) == 2 and result.model.startswith("typesafe/jev-1.13")


def test_auth_and_credit_errors_are_not_retried():
    with pytest.raises(JevAuthError, match="401"):
        client_with((401, {"error": {"code": 401, "message": "Missing Authentication header"}})).decide(body())
    with pytest.raises(JevAuthError, match="402"):
        client_with((402, {"error": {"code": 402, "message": "Insufficient credits"}})).decide(body())


def test_no_endpoint_error_suggests_no_zdr():
    message = {"error": {"code": 404, "message": "No endpoints found matching your data policy"}}
    with pytest.raises(JevError, match="--no-zdr"):
        client_with((404, message)).decide(body())


def test_missing_or_mistyped_answers_are_rejected():
    missing = {**OK, "answers": {"is_example": OK["answers"]["is_example"]}}
    with pytest.raises(JevError, match="missing 1 answer"):
        client_with((200, missing)).decide(body())
    mistyped = {**OK, "answers": {**OK["answers"], "count": {"type": "noul", "noul": 0.5}}}
    with pytest.raises(JevError, match="expected choice"):
        client_with((200, mistyped)).decide(body())


def test_request_bodies_split_by_token_budget(monkeypatch):
    many = {f"q{i}": {"type": "noul", "instructions": "x" * 300} for i in range(10)}
    monkeypatch.setattr(jev, "REQUEST_TOKEN_BUDGET", 400)
    bodies = request_bodies({"accomplishment": "Did a thing."}, many, "m", zdr=False)
    assert len(bodies) > 1
    assert sum(len(b["questions"]) for b in bodies) == 10
    assert all(b["state"] == {"accomplishment": "Did a thing."} for b in bodies)
    assert bodies[0]["provider"] == {"data_collection": "deny"}
