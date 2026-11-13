# ADR-013: Runtime Boundary, Observation Authority, and Cleanup Retry

- **Status:** Accepted
- **Date:** 2026-11-13
- **Owners:** ARTEMIS architecture maintainers
- **Blocks:** PR-R3.6.1, PR-R3.6.2, PR-R3.7.1, PR-R3.7.2
- **Related:** ADR-003, ADR-004, ADR-011; `ARCHITECTURE_CONTRACT.md`; `REFACTOR_EXECUTION_PLAN.md`

## 1. Context

The static import graph was clean, but runtime hand-offs still exposed a Controller:

```text
FlashRunner
  -> ActionService.__getattr__("controller")
  -> McpActionPort.__getattr__("controller")
  -> McpActionTransport.controller
  -> Actuator.controller
```

Observation also had two contracts (`ObserveResult` and `ScreenData`) and several Agent paths still called `get_screen_data(ctx)`, which dynamically found a Controller. Action receipts could be synthesized from tool success, and Session cleanup could clear lease ownership before physical cleanup was confirmed.

## 2. Decision

1. `ObservationPort` is the only Agent/Graph observation dependency.
   - It exposes `screen_data() -> ScreenData` for raw, evidence-bearing observations.
   - It exposes `observe(...) -> (ObserveResult, bytes)` for the persisted observation path.
   - `observe()` owns DataEngine image persistence; helpers must not persist a second copy.
   - A present `DeviceSession` is authoritative. Its failure never falls back to a Controller.
2. Agent code receives only explicit services. `ActionService` and `McpActionPort` do not forward unknown attributes; `McpActionTransport` does not expose a Controller; `FlashRunner` has no `controller` member.
3. Action receipts are produced only by a real `ActionDispatchResult`. Tool/helper success, legacy `session.call()`, and extension calls produce `action_state=unknown` and no `ActionReceipt`.
4. Device I/O faults are classified at the adapter boundary as `DeviceFault` / `DeviceDisconnectedError`; `DeviceSession.record_fault()` records the first fatal fault and transitions to `FAILED`. Non-device errors (OCR, parse, arguments) do not fail the Session.
5. `SessionState.CLOSED` means business shutdown, not physical cleanup completion. `CleanupState` and `SessionCloseReport` report `released_now`, `released_total`, retained resources, participant failures, and retryability.
6. `DeviceSession` closes registered external cleanup owners (Action/MCP) before hardware participants; Lease release is blocked until every hardware-holding participant is confirmed closed. `retry_cleanup()` retries only failed work.
7. UVC identity inventory is host-wide and tri-state (`UNAVAILABLE`/`EMPTY`/`OK`). Serial uniqueness is checked against the full host inventory, port paths are unique-checked, and `port_hint` is an exact physical selector. Public UVC errors inherit `ValueError` for compatibility.
8. `port_hint` is supplied by CLI `--uvc-port-hint`, `ARTEMIS_UVC_PORT_HINT`, or `IosRuntimeConfig.uvc_port_hint`; it is not `ARTEMIS_UVC_IDENTITY_KEY`.

## 3. Consequences

- Agents can be tested with a bomb Controller that raises on any attribute access.
- A Session failure is visible before `close()`; evidence records `FAILED` before `CLOSED`.
- Failed leases remain owned and retryable; a new Session cannot silently acquire a resource whose old handle is unconfirmed.
- UVC ambiguity is rejected instead of sorted-first guessing; the current capture card without serial/topology remains an explicit `CONFIG_HINT` fallback.
- The Session close API returns a report; callers that ignore the return keep working. Cancellation writes `last_close_report` before re-raising `CancelledError`.

## 4. Verification

- `tests/unit/mcp/test_observation_port.py`: session authority, no dynamic controller discovery, `screen_data`.
- `tests/unit/actions/test_action_service.py`, `tests/unit/actions/test_action_transport.py`: explicit API and real receipt.
- `tests/unit/architecture/test_action_contract_boundaries.py`: source/runtime boundary guards.
- `tests/unit/devices/test_session.py`: cleanup report, retry, external owner barrier, fatal fault.
- `tests/unit/runtime/test_resource_lease.py`: unlink failure retains owner and is retryable.
- `tests/unit/core/test_canonical_identity.py`, `tests/unit/drivers/test_ios_runtime_config.py`: inventory/hint rules.
- Real iOS smoke when the rig is connected; otherwise mark real-device evidence pending.

## 5. Rollback

Revert the relevant PR together with its tests. Do not restore Controller passthrough or lease clearing-before-unlink behavior. If observation port migration must be rolled back, keep the explicit `DeviceSession` authority rule and revert only the reporting shape.
