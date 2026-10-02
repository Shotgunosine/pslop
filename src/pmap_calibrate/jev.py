"""Client for Jev through OpenRouter's Decisions API (POST /api/alpha/decisions)."""

import random
import ssl
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Annotated, Literal

import httpx
import truststore
from pydantic import BaseModel, Field, ValidationError

from .questions import estimate_tokens

DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
# Jev allows 64k tokens per request (32k for state plus the longest question); stay well under.
REQUEST_TOKEN_BUDGET = 24_000
STATE_TOKEN_LIMIT = 30_000
RETRY_STATUSES = {429, 500, 502, 503, 524, 529}
# Listed price for typesafe/jev-1.13 on 2026-10-02; used only for dry-run estimates.
PRICE_PER_MILLION_INPUT_TOKENS = 0.042


class NoulAnswer(BaseModel):
    type: Literal["noul"]
    noul: float


class ChoiceAnswer(BaseModel):
    type: Literal["choice"]
    choice: str
    confidence: float | None = None
    probabilities: dict[str, float] = {}


class ScoreAnswer(BaseModel):
    type: Literal["score"]
    score: float
    confidence: float | None = None
    probabilities: dict[str, float] = {}


Answer = Annotated[NoulAnswer | ChoiceAnswer | ScoreAnswer, Field(discriminator="type")]


class Usage(BaseModel):
    input_tokens: int
    output_tokens: int
    cost: float | None = None


class DecisionsResponse(BaseModel):
    id: str | None = None
    model: str
    provider: str | None = None
    answers: dict[str, Answer]
    usage: Usage


class JevError(Exception):
    pass


class JevAuthError(JevError):
    pass


def provider_preferences(zdr: bool) -> dict:
    # Only route to providers that don't store or train on prompts.
    prefs = {"data_collection": "deny"}
    if zdr:
        prefs["zdr"] = True
    return prefs


def request_bodies(state, questions: dict, model: str, zdr: bool) -> list[dict]:
    """Split one state's questions into as many request bodies as the token budget needs."""
    def body(chunk: dict) -> dict:
        return {"model": model, "state": state, "questions": chunk, "provider": provider_preferences(zdr)}

    bodies, chunk = [], {}
    base = size = estimate_tokens(body({}))
    for key, question in questions.items():
        cost = estimate_tokens({key: question})
        if chunk and size + cost > REQUEST_TOKEN_BUDGET:
            bodies.append(body(chunk))
            chunk, size = {}, base
        chunk[key] = question
        size += cost
    if chunk:
        bodies.append(body(chunk))
    return bodies


class JevClient:
    def __init__(self, api_key: str, model: str, *, zdr: bool = True, timeout: float = 120.0,
                 max_attempts: int = 5, workers: int = 8, http: httpx.Client | None = None):
        self.model = model
        self.zdr = zdr
        self.max_attempts = max_attempts
        self.workers = workers
        # truststore uses the operating system's certificates, which networks that inspect TLS
        # (such as NIH's) require.
        self._http = http or httpx.Client(
            timeout=timeout, verify=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        )
        self._headers = {"Authorization": f"Bearer {api_key}"}

    def decide(self, body: dict) -> DecisionsResponse:
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = self._http.post(DECISIONS_URL, json=body, headers=self._headers)
            except httpx.TransportError as err:
                if attempt == self.max_attempts:
                    raise JevError(f"Could not reach OpenRouter: {err}") from err
                self._sleep(attempt, None)
                continue
            if response.status_code == 200:
                return self._parse(response, body)
            if self._retryable(response) and attempt < self.max_attempts:
                self._sleep(attempt, response.headers.get("retry-after"))
                continue
            raise self._error(response)
        raise AssertionError("unreachable")

    def decide_all(self, bodies: list[dict]) -> list[DecisionsResponse]:
        """Send requests concurrently; results come back in the same order."""
        if len(bodies) <= 1 or self.workers <= 1:
            return [self.decide(body) for body in bodies]
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            return list(pool.map(self.decide, bodies))

    def _parse(self, response: httpx.Response, body: dict) -> DecisionsResponse:
        try:
            result = DecisionsResponse.model_validate_json(response.content)
        except ValidationError as err:
            raise JevError(f"Unexpected Decisions response: {str(err)[:500]}") from err
        missing = set(body["questions"]) - set(result.answers)
        if missing:
            raise JevError(f"Decisions response is missing {len(missing)} answer(s), e.g. {sorted(missing)[0]}")
        for key, question in body["questions"].items():
            if result.answers[key].type != question["type"]:
                raise JevError(f"Answer {key} has type {result.answers[key].type}, expected {question['type']}")
        return result

    @staticmethod
    def _retryable(response: httpx.Response) -> bool:
        if response.status_code in RETRY_STATUSES:
            return True
        # A 402 from the in-flight spending budget clears on its own; other 402s mean no credits.
        return response.status_code == 402 and "openrouter_in_flight_budget" in response.text

    @staticmethod
    def _sleep(attempt: int, retry_after: str | None) -> None:
        try:
            delay = float(retry_after) if retry_after else None
        except ValueError:
            delay = None
        time.sleep(delay if delay and delay > 0 else min(30.0, 2 ** (attempt - 1)) + random.random())

    def _error(self, response: httpx.Response) -> JevError:
        message = _error_message(response)
        status = response.status_code
        if status == 401:
            return JevAuthError(f"OpenRouter rejected the API key (401): {message}. Check OPENROUTER_API_KEY.")
        if status == 402:
            return JevAuthError(f"OpenRouter reports insufficient credits or key limit (402): {message}")
        hint = ""
        if self.zdr and status in (400, 404) and ("endpoint" in message.lower() or "polic" in message.lower()):
            hint = (" If no zero-data-retention endpoint is available for this model, rerun with --no-zdr "
                    "('data_collection: deny' still applies).")
        return JevError(f"OpenRouter returned HTTP {status}: {message}.{hint}")


def _error_message(response: httpx.Response) -> str:
    try:
        error = response.json().get("error", {})
        return str(error.get("message") or error) if isinstance(error, dict) else str(error)
    except ValueError:
        return response.text[:300] or response.reason_phrase
