# ARTEMIS Current Architecture Snapshot — 2026-11-13

- **Branch:** `integration/10101205-all-fixes`
- **Code baseline:** `1daf3cf` (Host cleanup recovery lifecycle); docs HEAD may be newer
- **Normative contract:** [`ARCHITECTURE_CONTRACT.md`](ARCHITECTURE_CONTRACT.md)
- **Historical execution plan:** [`REFACTOR_EXECUTION_PLAN.md`](REFACTOR_EXECUTION_PLAN.md)
- **Current test baseline:** [`TEST_BASELINE.md`](TEST_BASELINE.md)
- **Historical audit:** [`POST_MIGRATION_ARCHITECTURE_AUDIT.md`](POST_MIGRATION_ARCHITECTURE_AUDIT.md) (pre-R3.6 snapshot)

This page records the **implemented architecture**, not a future target. It supersedes
only the “current implementation” claims in older audits; the architecture contract
remains normative.

## 1. Layer map

```text
L1 Access / Composition
  CLI, MCP, SDK, interfaces/action_bootstrap.py, interfaces/action_messages.py
        |
        v
L2 Agent / Orchestration
  agents/{flash,planner,checker,validator,operator}, graph/
        |
        v
L3 Interaction Intelligence
  perception/observation.py, actions/{service,device_actions},
  core/{action_port,actions,errors,capabilities}, control contracts, safety/
        |
        v
L4 Device Runtime
  devices/session.py, drivers/, adapters/, devices/registry,
  runtime/{resource_lease,session_cleanup}, firmware/esp32-hid/
        |
        v
L5 Target / Host Environment
  Android, iOS/iPadOS, macOS, Windows/Linux, TV, USB/HID/ADB/UVC stacks
```

`artemis/platform/` remains the **Host OS PAL**, not a controlled-device Adapter.

## 2. Current runtime composition

### 2.1 L1 composition

`artemis/interfaces/action_bootstrap.py::attach_action_service`:

1. If no `ctx.observation_port`:
   - `DeviceSession` present → `SessionObservationPort(ctx)`;
   - no Session → explicit `ControllerObservationPort(ctx, controller)`.
2. Build `ActionService(ctx, McpActionPort(McpActionTransport(ctx)), observation_port=...)`.
3. Use `McpActionExecutor` only as the explicit helper port.
4. Register `action_service.close` in the Session cleanup barrier when a Session exists.
5. Publish `ctx.reflex_tool_declarations`.

`AgentBase._init_internal` creates one `SessionCleanupRegistry` per Host Agent and
passes it to `ArtemisContext.cleanup_registry`.

### 2.2 Action path

```text
FlashRunner / Validator
  -> ActionService.execute
      -> DeviceActionService for manifest device actions
          -> ActionDispatchPort.dispatch(ActionCommand)
              -> McpActionPort
                  -> McpActionTransport
                      -> ActionSession / actuator / DeviceSession adapter
      -> explicit helper_port for read_note/list_notes/ask_explorer/reflex/history
```

- `ActionService` has only explicit members (`ctx`, `port`, `observation_port`,
  `helper_port`, `_device_service`, `action_tool_names`, `execute`, `close`).
- No `ActionService.__getattr__` / `__setattr__`.
- No `McpActionPort.__getattr__`.
- No `McpActionTransport.controller`; its actuator is private `_actuator`.
- Flash has no `self.controller`; it holds only `ActionService` and ObservationPort.
- `ActionService.execute` does not synthesize a device receipt from tool success.
- `ActionDispatchResult.receipt` is the only device-receipt source.
- Helper success has no ActionReceipt; a helper-provided receipt is a contract error.
- Legacy `session.call` / extension paths are marked legacy and use `action_state=unknown`.

### 2.3 Observation path

```text
Planner / Checker / Graph / Flash / Validator
  -> ctx.observation_port.observe()       # mandatory; L1-injected
       -> SessionObservationPort          # DeviceSession authoritative
       -> ControllerObservationPort       # only when L1 chose no-Session compat
            |
            +-> _observe_from_port()
                  -> port.screen_data()
                  -> DataEngine.get_or_create_image()
                  -> ObserveResult + screenshot bytes
```

- `ObservationPort` declares `screen_data()` and `observe()`.
- Agent/Graph code never calls `get_screen_data(ctx)`; a missing port is an error.
- `get_screen_data()` remains a lower-level compatibility helper outside Agent code.
- A present Session is authoritative; no fallback to Controller on Session failure.
- Planner/Graph no longer persist their own second image artifact.
- `ObservationProcessingError` separates OCR/XML/decode/model failures from device faults.

### 2.4 Session and cleanup

```text
NEW -> READY <-> DEGRADED -> CLOSING -> CLOSED
                         \-> FAILED (terminal business state)

CleanupState:
  NOT_STARTED -> IN_PROGRESS -> COMPLETE
                         \-> RETRYABLE
                         \-> MANUAL_REQUIRED
```

`DeviceSession` owns:
- a stable `runtime_id` UUID when no explicit `session_id` exists;
- ordered control/perception participants;
- a `LeaseSet`;
- optional external cleanup owners (Action/MCP);
- `SessionCloseReport` with released/retained/failed/closed participants,
  current `errors`, and `previous_errors`;
- `retry_cleanup()` and manual-required marking.

Close order:

```text
external cleanup owners (Action/MCP)
  -> control adapters
  -> perception adapters
  -> only if every hardware holder is confirmed closed: release leases
  -> report; CLOSED
```

Outer cancellation is deferred: adapter close is shielded and completed before
lease release/re-raise. Failed `safe_unlink()` keeps the Lease owner/`_HELD`
state and is retried; `LeaseSet` removes only successfully released leases.

`SessionCleanupRegistry` is Host Runtime owned and receives incomplete Sessions
from `ArtemisContext.__aexit__` before the Context releases its active reference.
`AgentBase._recover_pending_sessions()` runs one bounded `retry_pending()` pass
after each task and during `clean()`; unrecovered sessions are logged as
`MANUAL_REQUIRED`. Context cleanup uses a capture boundary so unexpected ordinary
exceptions are registered before the active reference is released.

### 2.5 Device fault classification

```text
DeviceDisconnectedError / TRANSPORT_FAILED -> immediate FAILED
DeviceFaultKind.CAPTURE_UNHEALTHY           -> after bounded unhealthy frames
ObservationProcessingError                  -> observation failure, Session stays usable
```

`DeviceSession.snapshot()`:
- `DeviceFault` → `record_fault()` and raise;
- `CaptureError(decode_error)` / OCR / XML / generic processing → processing error;
- black/non-ok frame → capture-unhealthy counter;
- a healthy source still wins over a non-fatal source failure;
- a fatal source fault is not hidden by a healthy source.

Control transport faults are reported by `McpActionTransport` through
`ActionResult.incident=transport_error` and `DeviceSession.record_fault()`.

### 2.6 UVC identity and configuration

```text
CLI --uvc-port-hint
  > ARTEMIS_UVC_PORT_HINT
  > ios.uvc_port_hint in JSONC
  > None
```

- `IosProfileConfig` validates the optional `ios` section; invalid config raises
  `IosConfigError`, never silently degrades.
- `probe_usb_inventory_result()` validates the `SPUSBDataType` JSON envelope:
  - valid empty list → `EMPTY`;
  - valid data → `OK`;
  - malformed/unavailable → `UNAVAILABLE`.
- Resolver checks serial uniqueness over the full Host inventory, then unique
  port path, then an exact `port_hint`; ambiguous candidates fail.
- UVC public errors inherit `ValueError`.
- The current capture card has no serial/topology, so live identity remains
  explicit `CONFIG_HINT`.

### 2.7 Receipt / goal semantics

```text
CREATED / DISPATCHED -> ADAPTER_ACKNOWLEDGED -> OBSERVED_EFFECT
                                              -> VERIFIED_SUCCESS (Checker only)
```

- Adapter ACK is not UI effect.
- `ActionEvidence.action_receipt` defaults to `{}`.
- Flash may promote an action to `observed_effect` only with effect evidence.
- `RunOutcome.goal_verified` is written only by the final Checker path.
- `ActionEvidence.goal_verified` remains false for action records.

## 3. Current completion status

| Area | Status | Evidence |
|---|---|---|
| Agent → Controller runtime cut | Complete | no `.controller` access in Agent/Graph; architecture tests |
| ObservationPort injection | Complete | `ctx.observation_port` required; no `get_screen_data` in Agent/Graph |
| ActionReceipt source | Complete | only `ActionDispatchResult`; helper/legacy no ACK |
| LangChain DTO isolation | Complete | `actions/results.py` has no LangChain import; interface adapter |
| Session cleanup state/report/retry | Complete | `CleanupState`, `SessionCloseReport`, retry/manual tests |
| Host cleanup recovery | Complete | bounded `retry_pending` after task/clean, manual-required result |
| Host CleanupRegistry | Complete | AgentBase creates/injects; Context registration test |
| Lease failure retention | Complete | `safe_unlink=False` and retry tests |
| UVC inventory/config | Complete for code; real multi-card unavailable | schema/config/CLI/factory tests |
| Device fault classification | Complete | processing/capture/transport tests |
| iOS/Android normal paths | Real-device PASS before latest minor docs | smoke/Flash/Pro, fault-injection report |
| R3.8 real matrix | Partial | AND-02 TCP unavailable; multi-card/hardware unlink via one-card rig limited |
| macOS / TV | Not started | ADR-012 Proposed; Phase 7/8 deferred |

## 4. Known boundaries

- `McpActionExecutor` remains a legacy L1 helper implementation; it still has an
  internal Controller but is not exposed to Agent code. Retiring it is separate.
- `BaseDeviceDriver` / `UnifiedMobileController` remain compatibility facades for
  some non-Agent paths; they are not the Agent observation contract.
- Validator `_absorb_post_screenshot` still writes an action post screenshot to
  DataEngine; this is action-evidence absorption, not the Agent ObservationPort path.
- `get_screen_data()` remains public for non-Agent/legacy callers; Agent/Graph
  callers are forbidden by architecture tests.
- `tests/unit/architecture/test_observation_injection_identity.py` proves Planner,
  Checker, Graph and ActionService all consume the exact same
  `ctx.observation_port` object instance, not merely four values read separately.
- No real multi-capture-card selection, TCP ADB, or real `safe_unlink` failure has
  been demonstrated on hardware.
