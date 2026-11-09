# ADR-NNN: <Decision title>

- **Status:** Proposed
- **Date:** YYYY-MM-DD
- **Owners:** <decision owner>
- **Blocks:** <PR / Phase>
- **Related:** <contract sections, code paths, other ADRs>

## 1. Context and problem

Describe the current behavior and why a decision is needed. Include exact repository paths, call chains, tests, and constraints. Distinguish observed facts from assumptions.

## 2. Decision drivers

List the requirements and trade-offs that matter: correctness, portability, latency, safety, operability, compatibility, testability, and migration cost.

## 3. Considered options

### Option A — <name>

- Benefits:
- Costs / risks:

### Option B — <name>

- Benefits:
- Costs / risks:

## 4. Proposed / accepted decision

State one precise decision. Use **Proposed** while awaiting approval. Once accepted, write the enforceable rule and its scope here.

## 5. Consequences

### Positive

-

### Negative / trade-offs

-

### Follow-up work

-

## 6. Contract and dependency impact

- Layers / domains:
- Ports / DTOs / Capability IDs:
- Adapters / Profiles:
- Import/dependency direction:
- Compatibility facade / public API:

## 7. Migration plan

List small PRs, ordering, compatibility period, and explicit non-goals.

## 8. Verification and evidence

- Unit / Contract tests:
- Integration tests:
- Hardware / platform evidence:
- Observability / trace fields:
- Criteria for marking capabilities `verified`:

## 9. Rollback

Describe how to revert safely without leaving stale locks, half-open device handles, schema incompatibility, or changed public behavior.

## 10. Security and safety

Address permissions, sensitive screen data, non-idempotent actions, resource ownership, cancellation, and fail-safe behavior.

## 11. Decision log

| Date | Status | Change | Rationale |
|---|---|---|---|
| YYYY-MM-DD | Proposed | Initial proposal | |
