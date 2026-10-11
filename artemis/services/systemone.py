"""TypeSafe "System One" (Jev) client: typed questions over a text state.

Why a separate client instead of a chat model
---------------------------------------------
Jev is not a chat LLM. Its own documentation is explicit: no message list, no text
generation, no tool calling, text-only input, and it is *"not a drop-in replacement
for the LLM behind a coding agent"*. It takes ``state`` + typed questions
(``noul`` / ``choice`` / ``score``) and returns typed answers with probabilities.

So it does not belong in :mod:`artemis.services.llm` (which builds LangChain chat
models). It belongs where a **judgement** or a **choice** is needed, on text: the
checker's verdicts, a classifier, a policy that ranks candidate moves. This client
is that entry point, and nothing else in the framework depends on it.

Configuration
-------------
``TYPESAFE_API_KEY`` — put it in ``.env`` at the repository root (gitignored) or
export it where the process runs. Optional overrides: ``TYPESAFE_BASE_URL``,
``TYPESAFE_MODEL`` (default ``jev-latest``), ``TYPESAFE_TIMEOUT_S``.
"""

from __future__ import annotations

import json
import os
from typing import Any
import urllib.error
import urllib.request

from third_party.mobile_use.utils.logger import get_logger

logger = get_logger(__name__)

ENV_API_KEY = "TYPESAFE_API_KEY"
ENV_BASE_URL = "TYPESAFE_BASE_URL"
ENV_MODEL = "TYPESAFE_MODEL"
ENV_TIMEOUT = "TYPESAFE_TIMEOUT_S"

DEFAULT_BASE_URL = "https://api.typesafe.ai"
DEFAULT_MODEL = "jev-latest"
DEFAULT_TIMEOUT_S = 20.0
#: Status codes the vendor tells you to retry rather than treat as a bug.
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 529})


class SystemOneUnavailable(RuntimeError):
    """No usable System One call: missing key, transport failure, or a bad answer.

    Callers treat this as "use the deterministic path" — never as a task failure.
    """


def dotenv_path() -> os.PathLike[str] | str:
    """Where the optional ``.env`` lives — a seam, so tests can point elsewhere.

    Configuration discovery that cannot be redirected is configuration discovery
    that cannot be tested: a workspace with a real key in ``.env`` made the
    "unconfigured" tests pass for the wrong reason.
    """
    from pathlib import Path as _Path

    from artemis.config import ROOT_DIR

    return _Path(str(ROOT_DIR)) / ".env"


def api_key() -> str | None:
    """The configured key, from the environment or ``.env`` at the repo root."""
    value = os.environ.get(ENV_API_KEY)
    if value and value.strip():
        return value.strip()
    try:
        from dotenv import load_dotenv

        load_dotenv(str(dotenv_path()), override=False)
    except (OSError, ImportError, ValueError) as exc:
        logger.debug(f"System One: could not read .env: {exc}")
        return None
    value = os.environ.get(ENV_API_KEY)
    return value.strip() if value and value.strip() else None


def is_configured() -> bool:
    return api_key() is not None


def base_url() -> str:
    return (os.environ.get(ENV_BASE_URL) or DEFAULT_BASE_URL).rstrip("/")


def model_name() -> str:
    return os.environ.get(ENV_MODEL) or DEFAULT_MODEL


def timeout_s() -> float:
    raw = os.environ.get(ENV_TIMEOUT)
    try:
        return float(raw) if raw else DEFAULT_TIMEOUT_S
    except ValueError:
        return DEFAULT_TIMEOUT_S


def ask(
    state: Any,
    questions: dict[str, dict[str, Any]],
    *,
    model: str | None = None,
    timeout: float | None = None,
    max_attempts: int = 3,
) -> dict[str, Any]:
    """Evaluate ``state`` against ``questions``; return ``{question_id: answer}``.

    ``questions`` uses the vendor's shape, e.g.::

        {"is_best": {"type": "noul", "instructions": "Is this the best placement?"}}

    Raises :class:`SystemOneUnavailable` when the call cannot be made or the answer
    cannot be read — callers fall back rather than propagate.
    """
    key = api_key()
    if not key:
        raise SystemOneUnavailable(
            f"{ENV_API_KEY} is not set (put it in .env at the repository root)"
        )
    if not questions:
        raise SystemOneUnavailable("no questions were asked")

    body = json.dumps(
        {"state": state, "model": model or model_name(), "questions": questions},
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url()}/v1/systemone",
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )

    import time

    delay = 0.5
    last_error = "no attempt was made"
    attempts = max(1, int(max_attempts))
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout or timeout_s()) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last_error = f"HTTP {exc.code}"
            if exc.code not in RETRYABLE_STATUS or attempt == attempts:
                raise SystemOneUnavailable(f"System One call failed: {last_error}") from exc
            retry_after = exc.headers.get("retry-after") if exc.headers else None
            sleep_for = (
                float(retry_after) if (retry_after or "").replace(".", "", 1).isdigit() else delay
            )
            logger.warning(f"System One: {last_error}; retrying in {sleep_for:.1f}s")
            time.sleep(sleep_for)
            delay = min(delay * 2, 8.0)
            continue
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt == attempts:
                raise SystemOneUnavailable(f"System One call failed: {last_error}") from exc
            time.sleep(delay)
            delay = min(delay * 2, 8.0)
            continue

        answers = payload.get("answers") if isinstance(payload, dict) else None
        if not isinstance(answers, dict):
            raise SystemOneUnavailable(f"System One returned no answers: {str(payload)[:200]}")
        return answers
    raise SystemOneUnavailable(f"System One call failed: {last_error}")


def noul(state: Any, instructions: str, *, question_id: str = "noul", **kwargs: Any) -> float:
    """A single true/false question; returns the probability of "true"."""
    answers = ask(state, {question_id: {"type": "noul", "instructions": instructions}}, **kwargs)
    return probability(answers.get(question_id))


def choice(
    state: Any,
    instructions: str,
    criteria: dict[str, str],
    *,
    question_id: str = "choice",
    **kwargs: Any,
) -> tuple[str | None, dict[str, float]]:
    """Pick one of ``criteria``; returns ``(winner, per-option probabilities)``."""
    answers = ask(
        state,
        {question_id: {"type": "choice", "instructions": instructions, "criteria": criteria}},
        **kwargs,
    )
    answer = answers.get(question_id)
    if not isinstance(answer, dict):
        raise SystemOneUnavailable("System One returned no choice answer")
    probabilities = answer.get("probabilities")
    distribution = (
        {str(k): float(v) for k, v in probabilities.items()}
        if isinstance(probabilities, dict)
        else {}
    )
    winner = answer.get("choice")
    return (str(winner) if winner is not None else None), distribution


def score(
    state: Any,
    instructions: str,
    criteria: list[str],
    *,
    question_id: str = "score",
    **kwargs: Any,
) -> float:
    """A rubric score; returns the probability-weighted value across the levels."""
    answers = ask(
        state,
        {question_id: {"type": "score", "instructions": instructions, "criteria": criteria}},
        **kwargs,
    )
    answer = answers.get(question_id)
    if not isinstance(answer, dict) or "score" not in answer:
        raise SystemOneUnavailable("System One returned no score answer")
    return float(answer["score"])


def probability(answer: Any) -> float:
    if isinstance(answer, dict):
        for key in ("noul", "probability", "confidence"):
            value = answer.get(key)
            if isinstance(value, (int, float)):
                return float(value)
    if isinstance(answer, (int, float)):
        return float(answer)
    raise SystemOneUnavailable(f"System One returned no noul answer: {str(answer)[:120]}")


__all__ = [
    "DEFAULT_MODEL",
    "ENV_API_KEY",
    "RETRYABLE_STATUS",
    "SystemOneUnavailable",
    "api_key",
    "ask",
    "base_url",
    "choice",
    "dotenv_path",
    "is_configured",
    "model_name",
    "noul",
    "probability",
    "score",
]
