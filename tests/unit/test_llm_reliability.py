"""Tests for the generic LLM failure taxonomy and circuit breaker."""

from artemis.llm.reliability import (
    CircuitBreaker,
    FailureCategory,
    classify_failure,
    extract_status_code,
    retry_policy_for,
)


def test_classifier_categories_and_recovery_decisions():
    rate = classify_failure(RuntimeError("429 rate limit exceeded"))
    assert rate.category is FailureCategory.RATE_LIMIT
    assert rate.retryable and rate.should_fallback

    outage = classify_failure(RuntimeError("503 service unavailable"))
    assert outage.category is FailureCategory.PROVIDER_UNAVAILABLE
    assert outage.retryable

    timeout = classify_failure(TimeoutError("request timed out"))
    assert timeout.category is FailureCategory.TIMEOUT
    assert timeout.retryable

    conn = classify_failure(ConnectionError("connection reset by peer"))
    assert conn.category is FailureCategory.CONNECTION
    assert conn.retryable

    auth = classify_failure(RuntimeError("401 unauthorized: invalid api key"))
    assert auth.category is FailureCategory.AUTHENTICATION
    assert not auth.retryable
    assert auth.should_fallback  # A fallback endpoint may hold a working key.

    class CodedError(Exception):
        code = 400

    bad = classify_failure(CodedError("invalid request payload"))
    assert bad.category is FailureCategory.BAD_REQUEST
    assert not bad.retryable
    assert not bad.should_fallback  # Falling back would only hide the bug.

    unknown = classify_failure(RuntimeError("something odd"))
    assert unknown.category is FailureCategory.UNKNOWN
    assert unknown.retryable


def test_openai_style_bad_request_stays_permanent_when_it_says_unavailable():
    """The HTTP status must beat the message markers.

    ``openai``'s APIError exposes ``.code`` from the response *body* — for
    DeepSeek that is the string ``"invalid_request_error"``, not a number — while
    the real status lives on ``.status_code``. Reading ``.code`` first and giving
    up left the code unknown, so the marker scan saw "unavailable" inside
    "This response_format type is unavailable now" and retried a permanently
    rejected request: the 2026-10-08 run burned three retries (5s/10s/20s) plus a
    circuit-breaker cooldown (~90s) before pausing, and the request-shape
    diagnostic gated on BAD_REQUEST never ran.
    """

    class OpenAIishBadRequest(Exception):
        code = "invalid_request_error"
        status_code = 400

    error = OpenAIishBadRequest(
        "Error code: 400 - {'error': {'message': 'This response_format type is"
        " unavailable now', 'code': 'invalid_request_error'}}"
    )

    assert extract_status_code(error) == 400
    failure = classify_failure(error)
    assert failure.category is FailureCategory.BAD_REQUEST
    assert not failure.retryable
    assert not failure.should_fallback


def test_status_code_is_used_when_code_is_not_numeric():
    class CodedStringError(Exception):
        code = "rate_limit_exceeded"
        status_code = 429

    assert extract_status_code(CodedStringError("slow down")) == 429
    assert classify_failure(CodedStringError("slow down")).category is FailureCategory.RATE_LIMIT


def test_a_numeric_code_is_still_honoured():
    class CodeOnlyError(Exception):
        code = 503

    assert extract_status_code(CodeOnlyError("boom")) == 503
    assert classify_failure(CodeOnlyError("boom")).category is FailureCategory.PROVIDER_UNAVAILABLE


def test_non_retryable_categories_get_single_attempt():
    assert retry_policy_for(FailureCategory.AUTHENTICATION).max_attempts == 1
    assert retry_policy_for(FailureCategory.BAD_REQUEST).max_attempts == 1
    assert retry_policy_for(FailureCategory.RATE_LIMIT).max_attempts > 1


def test_breaker_only_trips_on_transient_categories():
    breaker = CircuitBreaker(threshold=2, cooldown_seconds=10.0)
    auth = classify_failure(RuntimeError("401 unauthorized"))
    for _ in range(5):
        breaker.record_failure("m", auth, now=1.0)
    assert breaker.allow("m", now=1.5)

    outage = classify_failure(RuntimeError("503 unavailable"))
    breaker.record_failure("m", outage, now=2.0)
    breaker.record_failure("m", outage, now=3.0)
    assert not breaker.allow("m", now=4.0)
    assert breaker.open_remaining("m", now=4.0) > 0
    # Cooldown elapsed: half-open admits exactly one trial.
    assert breaker.allow("m", now=13.5)
    assert not breaker.allow("m", now=13.6)
    breaker.record_success("m")
    assert breaker.allow("m", now=13.7)


def test_retry_policy_accepts_extension_category_values():
    from enum import StrEnum

    class ExtCategory(StrEnum):
        RATE_LIMIT = "rate_limit"
        MEDIA_PROCESSING = "media_processing"

    assert retry_policy_for(ExtCategory.RATE_LIMIT) is retry_policy_for(FailureCategory.RATE_LIMIT)
    assert retry_policy_for("provider_unavailable") is retry_policy_for(
        FailureCategory.PROVIDER_UNAVAILABLE
    )
    # Extension-only categories fall back to the UNKNOWN policy; callers gate
    # on failure.retryable before consulting attempt counts.
    assert retry_policy_for(ExtCategory.MEDIA_PROCESSING) is retry_policy_for(
        FailureCategory.UNKNOWN
    )


def test_classifier_recognizes_genai_deadline_exceeded_shapes():
    # google-genai SDK timeouts can surface without a status code attached.
    for message in ("DEADLINE_EXCEEDED", "deadline exceeded while awaiting response"):
        failure = classify_failure(RuntimeError(message))
        assert failure.category is FailureCategory.TIMEOUT
        assert failure.retryable
