# Sensitive Data Redaction and Retention — Execution Plan

- **Status:** Proposed / not implemented
- **Date:** 2026-11-11
- **Scope:** Data Engine traces/artifacts, Telemetry spans, session export, capability artifacts and logs.
- **Authority:** This is an implementation plan. The data-ownership contract remains `ARCHITECTURE_CONTRACT.md` §8; this document does not change public Ports/DTOs until an ADR/implementation review.

## 1. Current state

Already present:

- `data_engine/trace.py::smart_serialize` replaces image bytes with `<ImageRef: sha256=…>`.
- Data Engine stores screenshots as SHA-256 image files, and Step/Image records keep OCR/AX payloads.
- Telemetry spans persist JSONL payloads.
- `CapabilityArtifactStore` is content-addressed with a 5 MB cap.
- Session export copies raw images from the database.

Not present:

- No central key/pattern redaction before trace/telemetry/step persistence.
- No retention sweeper for session images/videos or capability artifacts.
- No explicit sensitive-artifact mode in export.
- No policy tests proving a password/OTP/token never reaches persisted JSON.

## 2. Design principles

1. **Redact at persistence/export boundaries**, not in the live Agent context; redaction must not change a click target or text input used for the current action.
2. **Images cannot be regex-redacted reliably.** A screenshot may contain passwords, OTPs, private messages or account data. The only honest guarantee is an artifact mode that does not persist the raw image.
3. **Do not invent certainty:** if an image was not retained, Replay must say so; it must not silently look like a complete replay.
4. **One policy, many writers:** Data Engine, Telemetry, export and capability artifacts use the same DTO/rules.
5. **No password/token in ordinary logs**; redaction is defense in depth, not a reason to log secrets.
6. **Default is useful but not absolute:** `standard` preserves local screenshots with a short TTL/access control; `strict` drops raw image/OCR persistence.

## 3. Data classification

| Class | Examples | Default |
|---|---|---|
| Raw screen | screenshots, video, annotated crops | local `full` + TTL in standard; `metadata`/`none` in strict |
| Screen text | OCR, AX/UI tree, element labels | standard redacts secret-shaped fields; strict retains counts/hashes only |
| Action input | `input_text`, typed text, target description | redact value, retain length/hash |
| Tool args/results | trace payload, errors, URLs | key/pattern redaction |
| Telemetry | span payload, error, token usage | key/pattern redaction; no images |
| Capability artifact | smoke report/screenshot | separate TTL; evidence DB stays, artifact may expire |
| Logs | logger fields/messages | secret-key filter; no raw prompt/screen |

## 4. Policy DTO

Add `artemis/data_engine/privacy.py` (name may change after review):

```python
@dataclass(frozen=True)
class PrivacyPolicy:
    mode: Literal["off", "standard", "strict"] = "standard"
    redact_keys: frozenset[str]          # password/token/secret/otp/pin...
    redact_patterns: tuple[re.Pattern, ...]
    persist_screenshots: Literal["full", "metadata", "none"] = "full"
    session_retention_days: int = 30
    capability_artifact_retention_days: int = 90
```

Environment proposal:

```text
ARTEMIS_TRACE_REDACTION=standard|strict|off   # default standard
ARTEMIS_TRACE_SCREENSHOTS=full|metadata|none  # default full (local, TTL)
ARTEMIS_SESSION_RETENTION_DAYS=30
ARTEMIS_CAPABILITY_ARTIFACT_RETENTION_DAYS=90
```

`off` is development-only and must emit a startup warning. `strict` is the only mode that can honestly claim “raw screen content is not persisted.”

## 5. Redaction rules

### 5.1 Structured keys

Recursively walk dict/list. For keys matching (case-insensitive):

```text
password, passcode, passwd, pin, otp, token, access_token,
refresh_token, secret, api_key, authorization, cookie, session_cookie,
credit_card, card_number, cvv
```

replace the value with `<redacted>`; do not store a hash of low-entropy secrets (OTP/PIN/password).

### 5.2 Text patterns

Apply only to explicit secret shapes:

- `Bearer <token>`
- `api_key=…`, `token=…` in URLs/headers
- private key blocks / long base64 credentials
- optionally email/phone only in `strict`

Do not blanket-delete all OCR text; that makes replay and task diagnosis misleading.

### 5.3 Typed input

For `input_text` / `type` action evidence:

```json
{"text": "<redacted:len=12>", "text_sha256": "…"}
```

Keep length/hash for debugging. An explicit `--include-sensitive` export may retain the value only with an operator acknowledgement.

## 6. Artifact modes and retention

### `full`

- raw images/video stored below the session directory;
- never copied into ordinary Trace/Telemetry JSON;
- TTL cleanup by session age;
- export copies raw images only when `full` and explicitly allowed.

### `metadata`

- no raw image file;
- keep `ArtifactRef`/hash, width, height, health, byte count;
- Replay visibly reports “screen artifact unavailable”.

### `none`

- no image/hash artifact;
- action/step/incident counts remain.

### Cleanup

Add an idempotent `ArtifactRetention.cleanup(now)`:

- only closed sessions older than TTL;
- never delete an active session;
- check capability evidence references before deleting a capability artifact;
- evidence rows are not deleted with the session; a missing artifact makes replay explicitly incomplete;
- cleanup audit records counts, never screen content.

## 7. Write/export hooks

1. `DataEngine.record_trace`: `privacy.redact(payload)` before SQLite.
2. `DataEngine.record_step`: redact `operator_*_thinking`, `last_execution_result`, `extra_metadata`; OCR/AX follows screen mode.
3. `TelemetryTracer.record_span`: redact `payload`; never put images in a span.
4. `session_export`: apply redaction and artifact mode; mark missing/redacted evidence in `evidence.md`.
5. `CapabilityArtifactStore.put`: honor sensitivity/kind; reject known secret keys.
6. Logging filter: redact secret keys/headers; no full prompt/screen content.

## 8. Tests

- Nested dict/list key redaction; no mutation of the caller’s live object.
- `input_text` retains length/hash but not value.
- Trace/step/Telemetry payload never contains the secret fixture.
- `full` creates images; `metadata` creates only references; `none` creates none.
- Retention removes only expired closed sessions; active session survives.
- Capability artifact referenced by evidence is not deleted.
- Export in `standard` omits secrets; `strict` marks screenshots unavailable; explicit sensitive export is opt-in.
- Regression: target/coords/incident/ACK remain for Replay.

## 9. Rollout

| Step | Work | Behavior |
|---|---|---|
| 5a | policy + redaction pure functions/tests | none |
| 5b | Data Engine + Telemetry hooks, default standard | redacted persisted text |
| 5c | screenshot modes + retention sweeper + export | storage/retention change |
| 5d | fake-secret real-device test + baseline/audit update | evidence, not a capability claim |

## 10. Non-goals / limitations

- Cannot guarantee screenshot-level pixel redaction without OCR/region classification; use `metadata`/`none` for sensitive runs.
- Do not treat a hash as safe for low-entropy secrets.
- Do not delete capability evidence just because its artifact expired.
- No cloud access-control implementation in this plan; it only defines local persistence/export behavior.
