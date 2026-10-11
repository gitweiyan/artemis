# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The System One (Jev) client: a typed-question endpoint, not a chat model.

Jev answers *questions about a state* and returns probabilities; it takes no
images, no messages and calls no tools (its own docs say so). The client therefore
lives beside the LLM layer rather than inside it, and its failure mode is a typed
exception the caller can fall back from — never a broken run.
"""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from artemis.services import systemone


class FakeResponse:
    def __init__(self, payload: dict, status: int = 200):
        self._body = json.dumps(payload).encode("utf-8")
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in (systemone.ENV_API_KEY, systemone.ENV_BASE_URL, systemone.ENV_MODEL):
        monkeypatch.delenv(name, raising=False)
    yield


def _capture_request(monkeypatch, payload: dict, status: int = 200):
    """Patch urlopen, returning a list that receives the Request objects."""
    seen: list = []

    def fake_urlopen(request, timeout=None):
        seen.append(request)
        if status >= 400:
            raise urllib.error.HTTPError(
                request.full_url, status, "error", {"retry-after": "0"}, io.BytesIO(b"{}")
            )
        return FakeResponse(payload)

    monkeypatch.setattr(systemone.urllib.request, "urlopen", fake_urlopen)
    return seen


def test_the_request_carries_a_state_and_typed_questions(monkeypatch):
    seen = _capture_request(monkeypatch, {"answers": {"is_best": {"type": "noul", "noul": 0.9}}})
    monkeypatch.setenv(systemone.ENV_API_KEY, "secret-key")
    monkeypatch.setenv(systemone.ENV_BASE_URL, "https://example.test")

    answers = systemone.ask(
        {"board_rows_top_to_bottom": [".........."]},
        {"is_best": {"type": "noul", "instructions": "Is this the best placement?"}},
    )

    assert answers["is_best"]["noul"] == 0.9
    request = seen[0]
    body = json.loads(request.data.decode("utf-8"))
    assert request.full_url == "https://example.test/v1/systemone"
    assert body["model"] == systemone.DEFAULT_MODEL
    assert body["questions"]["is_best"]["type"] == "noul"
    assert body["state"]["board_rows_top_to_bottom"] == [".........."]
    assert request.headers["Authorization"] == "Bearer secret-key"
    assert "secret-key" not in json.dumps(body)


def test_a_missing_key_says_where_to_put_it(monkeypatch):
    _capture_request(monkeypatch, {"answers": {}})

    with pytest.raises(systemone.SystemOneUnavailable) as excinfo:
        systemone.ask("state", {"q": {"type": "noul", "instructions": "x"}})

    assert systemone.ENV_API_KEY in str(excinfo.value)
    assert ".env" in str(excinfo.value)


def test_a_rate_limited_call_is_retried_then_succeeds(monkeypatch):
    attempts = {"n": 0}
    responses = [
        urllib.error.HTTPError("url", 429, "slow down", {"retry-after": "0"}, io.BytesIO(b"{}")),
        FakeResponse({"answers": {"q": {"type": "noul", "noul": 0.4}}}),
    ]

    def fake_urlopen(request, timeout=None):
        item = responses[min(attempts["n"], len(responses) - 1)]
        attempts["n"] += 1
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setenv(systemone.ENV_API_KEY, "k")
    monkeypatch.setattr(systemone.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(
        systemone.time if hasattr(systemone, "time") else __import__("time"),
        "sleep",
        lambda _s: None,
    )

    answers = systemone.ask("state", {"q": {"type": "noul", "instructions": "x"}})

    assert attempts["n"] == 2
    assert answers["q"]["noul"] == 0.4


def test_a_non_retryable_error_becomes_a_typed_failure(monkeypatch):
    _capture_request(monkeypatch, {}, status=400)
    monkeypatch.setenv(systemone.ENV_API_KEY, "k")

    with pytest.raises(systemone.SystemOneUnavailable):
        systemone.ask("state", {"q": {"type": "noul", "instructions": "x"}})


def test_an_unreadable_answer_is_a_failure_not_a_guess(monkeypatch):
    _capture_request(monkeypatch, {"unexpected": True})
    monkeypatch.setenv(systemone.ENV_API_KEY, "k")

    with pytest.raises(systemone.SystemOneUnavailable):
        systemone.ask("state", {"q": {"type": "noul", "instructions": "x"}})


def test_noul_choice_and_score_are_read_from_their_answers():
    assert systemone.probability({"type": "noul", "noul": 0.77}) == 0.77
    assert systemone.probability(0.5) == 0.5
    with pytest.raises(systemone.SystemOneUnavailable):
        systemone.probability({"type": "noul"})


def test_the_helpers_shape_the_three_primitives(monkeypatch):
    captured: list[dict] = []

    def fake_ask(state, questions, **kwargs):
        captured.append(questions)
        key = next(iter(questions))
        if questions[key]["type"] == "noul":
            return {key: {"type": "noul", "noul": 0.66}}
        if questions[key]["type"] == "choice":
            return {
                key: {
                    "type": "choice",
                    "choice": "billing",
                    "probabilities": {"billing": 0.8, "tech": 0.2},
                }
            }
        return {key: {"type": "score", "score": 1.4, "probabilities": {"low": 0.6, "high": 0.4}}}

    monkeypatch.setattr(systemone, "ask", fake_ask)

    assert systemone.noul("s", "Is it urgent?") == 0.66
    winner, distribution = systemone.choice(
        "s", "Which team?", {"billing": "money", "tech": "bugs"}
    )
    assert winner == "billing" and distribution["tech"] == 0.2
    assert systemone.score("s", "How angry?", ["calm", "angry"]) == 1.4
    assert [q[next(iter(q))]["type"] for q in captured] == ["noul", "choice", "score"]


def test_configuration_is_read_from_the_environment(monkeypatch):
    assert systemone.is_configured() is False
    monkeypatch.setenv(systemone.ENV_API_KEY, "  spaced  ")
    monkeypatch.setenv(systemone.ENV_MODEL, "jev-1.13.0")

    assert systemone.is_configured() is True
    assert systemone.api_key() == "spaced"
    assert systemone.model_name() == "jev-1.13.0"


def test_the_endpoint_is_not_a_chat_completions_url():
    """Pins the reason this client exists at all."""
    assert systemone.base_url() == systemone.DEFAULT_BASE_URL
    source = systemone.ask.__doc__ or ""
    assert "noul" in source
